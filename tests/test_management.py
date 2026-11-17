"""Encrypted management edits, atomic rollback, revisions and HTTP boundary."""
from types import SimpleNamespace

from cryptography.fernet import Fernet
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from magicalgirl_web_oracle.admin.app import create_admin_app
from magicalgirl_web_oracle.admin.management import ConfigConflict,KeyCreate,KeyPatch,PoolConfirm
from magicalgirl_web_oracle.providers.key_pool import CredentialExecutor
from magicalgirl_web_oracle.quota.accounts import AccountConfig
from magicalgirl_web_oracle.quota.ledger import AccountLedger,BudgetUnavailable
from test_key_pool import account_config


@pytest.fixture
def managed(tmp_path,settings):
    settings.vault_key=Fernet.generate_key().decode()
    ledger=AccountLedger(str(tmp_path/"accounts.sqlite3"))
    executor=CredentialExecutor(settings,AccountConfig(),ledger,environ={})
    yield executor
    ledger.close()


def create(executor,secret="test-vault-secret-one",account_id=None):
    return executor.manager.create(KeyCreate(revision=executor.revision,label="Key A",secret=secret,
        provider="tavily",account_label="Google-B",account_id=account_id))


def test_credentials_are_encrypted_and_survive_restart(managed):
    key_id=create(managed)
    assert managed._secrets[key_id]=="test-vault-secret-one"
    assert "test-vault-secret-one" not in '\n'.join(managed.ledger.db.iterdump())
    restarted=CredentialExecutor(managed.settings,AccountConfig(),managed.ledger,environ={})
    assert restarted._secrets[key_id]=="test-vault-secret-one"
    assert restarted.revision==1 and len(restarted.config.accounts)==1


def test_no_master_or_wrong_master_fails_closed(managed):
    create(managed)
    previous=managed.settings.vault_key
    for wrong in ("",Fernet.generate_key().decode(),"not-a-valid-master"):
        managed.settings.vault_key=wrong
        with pytest.raises(ValueError):
            CredentialExecutor(managed.settings,AccountConfig(),managed.ledger,environ={})
    managed.settings.vault_key=previous
    assert managed.revision==1


def test_duplicate_key_rejected_atomically(managed):
    first=create(managed)
    with pytest.raises(ValueError):
        create(managed,account_id=managed.config.keys[0].account_id)
    assert managed.revision==1 and len(managed.config.keys)==1
    assert managed.ledger.db.execute("SELECT COUNT(*) FROM managed_secrets").fetchone()[0]==1
    assert managed._secrets[first]=="test-vault-secret-one"


def test_existing_account_shares_pool_and_revision_conflict_does_not_overwrite(managed):
    first=create(managed)
    account_id=managed.config.keys[0].account_id
    second=create(managed,"test-vault-secret-two",account_id)
    assert len(managed.config.accounts)==1 and len(managed.config.pools)==1
    assert managed.config.keys[0].pool_ids==managed.config.keys[1].pool_ids
    with pytest.raises(ConfigConflict):
        managed.manager.patch(first,KeyPatch(revision=1,label="stale"))
    assert managed.config.keys[0].label=="Key A" and managed.revision==2
    assert second!=first


def test_initial_unknown_pool_must_be_explicitly_confirmed_and_cannot_be_reset(managed):
    key_id=create(managed)
    key=managed.config.keys[0]
    assert managed.ledger.remaining(managed.config.pools[0])==0
    pool_id=key.pool_ids[0]
    managed.manager.confirm_pool(pool_id,PoolConfirm(revision=1,limit=1000,initial_used=250,
        window={"kind":"monthly","reset_day":1,"timezone":"UTC"},free_allowance_confirmed=True))
    assert managed.ledger.remaining(managed.pools[pool_id])==750
    call=managed.ledger.reserve(key,managed.pools,"tavily","search",1)
    managed.ledger.dispatch(call);managed.ledger.settle(call,1)
    with pytest.raises(ValueError,match="reconciliation"):
        managed.manager.confirm_pool(pool_id,PoolConfirm(revision=2,limit=1000,initial_used=0,
            window={"kind":"monthly","reset_day":1},free_allowance_confirmed=True))
    assert managed.ledger.remaining(managed.pools[pool_id])==749
    assert managed.revision==2


def test_disable_and_replace_preserve_key_identity_and_history(managed):
    key_id=create(managed)
    managed.manager.patch(key_id,KeyPatch(revision=1,enabled=False,label="Disabled"))
    assert managed.config.keys[0].id==key_id and not managed.config.keys[0].enabled
    assert managed.ledger.key_states()[key_id]["disabled"]==1
    managed.manager.patch(key_id,KeyPatch(revision=2,secret="replacement-vault-secret",enabled=True))
    assert managed._secrets[key_id]=="replacement-vault-secret"
    assert managed.ledger.key_states()[key_id]["disabled"]==0
    assert len(managed.config.keys)==1
    dump='\n'.join(managed.ledger.db.iterdump())
    assert "replacement-vault-secret" not in dump and "test-vault-secret-one" not in dump


def test_invalid_pool_confirmation_rolls_back_config_and_budget(managed):
    create(managed)
    pool=managed.config.pools[0]
    with pytest.raises(ValueError):
        managed.manager.confirm_pool(pool.id,PoolConfirm(revision=1,limit="0.0000001",initial_used=0,
            window={"kind":"monthly","reset_day":1},free_allowance_confirmed=True))
    assert managed.revision==1 and managed.pools[pool.id].window.kind=="unknown"


def test_second_executor_sees_persisted_revision(managed):
    second=CredentialExecutor(managed.settings,AccountConfig(),managed.ledger,environ={})
    key_id=create(managed)
    second.manager.reload()
    assert second.revision==1 and second._secrets[key_id]=="test-vault-secret-one"


def test_management_http_requires_explicit_action_and_secure_transport(managed):
    oracle=SimpleNamespace(registry=SimpleNamespace(credentials=managed))
    app=Starlette(routes=[Mount('/admin',create_admin_app(lambda:oracle,'admin-test'))])
    auth={"Authorization":"Bearer admin-test","X-Oracle-Action":"manage"}
    body={"revision":0,"label":"New Key","secret":"test-http-private-key","provider":"tavily","account_label":"Google-B"}
    with TestClient(app) as client:
        assert client.post('/admin/api/keys',json=body,headers=auth).status_code==403
    with TestClient(app,base_url='https://testserver') as client:
        assert client.post('/admin/api/keys',json=body).status_code==401
        assert client.post('/admin/api/keys',json=body,headers={"Authorization":"Bearer admin-test"}).status_code==403
        assert client.post('/admin/api/keys',json=body,headers={**auth,"Origin":"https://attacker.invalid"}).status_code==403
        result=client.post('/admin/api/keys',json=body,headers=auth)
        assert result.status_code==200 and 'test-http-private-key' not in result.text
        duplicate=client.post('/admin/api/keys',json={**body,"revision":1},headers=auth)
        assert duplicate.status_code==400 and 'test-http-private-key' not in duplicate.text
        data=client.get('/admin/api/overview',headers=auth).json()
        assert data['revision']==1 and data['management_enabled']
        assert 'test-http-private-key' not in str(data) and 'ciphertext' not in str(data)


def test_base_yaml_changes_require_explicit_reconciliation(managed):
    create(managed)
    with pytest.raises(ValueError,match="base account configuration changed"):
        CredentialExecutor(managed.settings,account_config(),managed.ledger,environ={"KEY_A":"fake-a","KEY_B":"fake-b"})


def test_old_inflight_failure_cannot_disable_replaced_credentials(managed):
    key_id=create(managed)
    key=managed.config.keys[0]
    old_fingerprint=managed.ledger.fingerprint('tavily',managed._secrets[key_id])
    managed.manager.patch(key_id,KeyPatch(revision=1,secret='replacement-for-race'))
    managed.ledger.penalize(key,'auth',fingerprint=old_fingerprint)
    assert managed.ledger.key_states()[key_id]['disabled']==0
    with pytest.raises(BudgetUnavailable,match='changed'):
        managed.ledger.reserve(key,managed.pools,'tavily','search',1,fingerprint=old_fingerprint)


def test_rotating_a_disabled_key_keeps_it_disabled_and_enable_still_works(managed):
    key_id=create(managed)
    managed.manager.patch(key_id,KeyPatch(revision=1,enabled=False))
    managed.manager.patch(key_id,KeyPatch(revision=2,secret='replacement-still-disabled'))
    state=managed.ledger.key_states()[key_id]
    assert state['disabled']==1 and state['reason']=='manual'
    managed.ledger.penalize(managed.config.keys[0],'rate_limit')
    assert managed.ledger.key_states()[key_id]['reason']=='manual'
    managed.manager.patch(key_id,KeyPatch(revision=3,enabled=True))
    assert managed.ledger.key_states()[key_id]['disabled']==0
