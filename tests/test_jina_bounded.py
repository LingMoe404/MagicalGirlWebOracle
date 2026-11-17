"""Bounded Reader, per-key wallets and non-transferable free budgets."""
from decimal import Decimal
import json

import httpx
import pytest

from magicalgirl_web_oracle.admin.management import KeyCreate,KeyPatch,PoolConfirm
from magicalgirl_web_oracle.models.schemas import FetchedDocument
from magicalgirl_web_oracle.providers.base import ProviderError
from magicalgirl_web_oracle.providers.jina import JinaReaderFetcher
from magicalgirl_web_oracle.providers.jina_bounded import BoundedJinaReader
from magicalgirl_web_oracle.providers.key_pool import CredentialExecutor,ManagedProvider
from magicalgirl_web_oracle.quota.accounts import BillingWindow
from test_key_pool import runtime
from test_management import managed


async def test_reader_posts_only_url_with_server_budget_and_preserves_fragment(settings):
    settings.jina_api_key='private-jina-key'
    provider=BoundedJinaReader(settings)
    seen=[]
    def handler(request):
        seen.append(request)
        assert request.method=='POST' and str(request.url)=='https://r.jina.ai/'
        assert request.headers['Authorization']=='Bearer private-jina-key'
        assert request.headers['X-Token-Budget']=='4096'
        assert request.headers['X-Return-Format']=='markdown'
        assert 'X-Respond-With' not in request.headers
        assert json.loads(request.content)=={'url':'https://example.com/#/文档'}
        return httpx.Response(200,text='Title: 测试\n正文',headers={'X-Usage-Tokens':'120'})
    provider._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result=await provider.fetch('https://example.com/#/文档',token_budget=4096)
    assert len(seen)==1 and result.title=='测试' and not result.metadata['keyless']
    assert provider.last_cost_tokens==120 and provider.last_cost_reported
    await provider.aclose()


@pytest.mark.parametrize('options',[{'token_budget':0},{'token_budget':32769},{'token_budget':True},{'token_budget':1.5},
    {'respond_with':'jina-ocr-v1'},{'headers':{'X-Respond-With':'readerlm-v2'}}])
async def test_unsafe_options_never_reserve_or_construct_provider(tmp_path,settings,options):
    _,ledger,executor=runtime(tmp_path,settings,family='jina',unit='tokens',limit=50000)
    class Never:
        def __init__(self,*args): raise AssertionError('no dispatch')
    with pytest.raises(ProviderError) as error:
        await executor.execute('jina_reader',Never,'fetch','https://example.com',**options)
    assert error.value.kind=='config' and ledger.calls()==[]
    ledger.close()


@pytest.mark.parametrize('usage,cost,source',[('100',100,'provider_reported'),('0',0,'provider_reported'),(None,8192,'conservative_estimate')])
async def test_executor_uses_real_usage_or_conservative_cap(tmp_path,settings,monkeypatch,usage,cost,source):
    cfg,ledger,executor=runtime(tmp_path,settings,family='jina',unit='tokens',limit=50000)
    def handler(request):
        assert request.headers['X-Token-Budget']=='8192'
        return httpx.Response(200,text='markdown',headers={} if usage is None else {'X-Usage-Tokens':usage})
    async def client(self):
        self._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return self._client
    monkeypatch.setattr(BoundedJinaReader,'client',client)
    provider=ManagedProvider(JinaReaderFetcher(settings),executor)
    assert provider.factory is BoundedJinaReader
    await provider.fetch('https://private.invalid')
    assert ledger.remaining(cfg.pools[0])==Decimal(50000-cost)
    detail=ledger.trace_detail(ledger.traces()[0]['id'])
    assert next(e for e in detail['events'] if e['event']=='settled')['details']['cost_source']==source
    assert 'private.invalid' not in json.dumps(detail)
    ledger.close()


async def test_old_shared_wallet_is_blocked_before_dispatch(tmp_path,settings):
    _,ledger,executor=runtime(tmp_path,settings,family='jina',unit='tokens',shared=True,limit=50000)
    class Never:
        def __init__(self,*args): raise AssertionError('no dispatch')
    with pytest.raises(ProviderError): await executor.execute('jina_reader',Never,'fetch','https://example.com')
    assert ledger.calls()==[]
    assert any(alert['kind']=='wallet_mismatch' for alert in executor.alerts.evaluate())
    assert any(e['details'].get('reason')=='wallet_mismatch' for e in ledger.trace_detail(ledger.traces()[0]['id'])['events'])
    ledger.close()


@pytest.mark.parametrize('response',[httpx.Response(500,text='private-token'),httpx.Response(200,text='body',headers={'X-Usage-Tokens':'NaN'})])
async def test_failure_keeps_reserved_budget_and_error_is_sanitized(tmp_path,settings,response):
    cfg,ledger,executor=runtime(tmp_path,settings,family='jina',unit='tokens',limit=8192)
    class Mock(BoundedJinaReader):
        async def client(self):
            self._client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req:response))
            return self._client
    with pytest.raises(ProviderError) as failure:
        await executor.execute('jina_reader',Mock,'fetch','https://private.invalid')
    assert all(ledger.remaining(pool)==0 for pool in cfg.pools)
    assert 'private-token' not in str(failure.value)
    assert all(call['state']=='uncertain' for call in ledger.calls())
    ledger.close()


async def test_target_422_does_not_penalize_key_or_provider(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,family='jina',unit='tokens',limit=50000)
    class Blocked(BoundedJinaReader):
        async def client(self):
            self._client=httpx.AsyncClient(transport=httpx.MockTransport(
                lambda req:httpx.Response(422,text='{"detail":"cannot fetch target"}')))
            return self._client
    with pytest.raises(ProviderError) as failure:
        await executor.execute('jina_reader',Blocked,'fetch','https://dead.invalid')
    # Jina is up; the target page is not. The key stays usable and the caller
    # sees a target-level failure that must not trip provider health.
    assert failure.value.affects_health is False
    assert all(row['cooldown']==0 for row in
               ledger.db.execute("SELECT cooldown FROM credentials").fetchall())
    # Billing stays conservative: the dispatched request is not assumed free.
    assert all(call['state']=='uncertain' for call in ledger.calls())
    ledger.close()


async def test_reader_never_follows_redirects_or_uses_keyless(settings):
    settings.jina_api_key='private-key';settings.jina_keyless_fallback=True
    provider=BoundedJinaReader(settings)
    seen=[]
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307,headers={'Location':'https://other.invalid'})
    provider._client=httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=True)
    with pytest.raises(ProviderError): await provider.fetch('https://example.com')
    assert seen==['https://r.jina.ai/']
    settings.jina_api_key=''
    with pytest.raises(ProviderError): await provider.fetch('https://example.com')
    assert len(seen)==1
    await provider.aclose()


def test_web_wallet_cannot_share_key_or_transfer_confirmed_budget(managed):
    key_id=managed.manager.create(KeyCreate(revision=0,label='Jina',provider='jina',account_label='钱包 A',secret='old-private-key'))
    key=managed.config.keys[0];pool=managed.config.pools[0]
    with pytest.raises(ValueError,match='independent wallet'):
        managed.manager.create(KeyCreate(revision=1,label='another',account_id=key.account_id,secret='new-private-key'))
    assert managed.revision==1
    managed.manager.confirm_pool(pool.id,PoolConfirm(revision=1,limit=50000,initial_used=0,
        window=BillingWindow(kind='one_time'),free_allowance_confirmed=True))
    with pytest.raises(ValueError,match='independent wallet'):
        managed.manager.patch(key_id,KeyPatch(revision=2,secret='different-wallet-key'))
    assert managed.revision==2 and managed._secrets[key_id]=='old-private-key'
    managed.manager.patch(key_id,KeyPatch(revision=2,label='renamed'))
    assert managed.config.keys[0].label=='renamed'


def test_environment_rotation_cannot_transfer_confirmed_wallet_budget(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,family='jina',unit='tokens',limit=50000)
    with pytest.raises(ValueError,match='confirmed wallet'):
        CredentialExecutor(settings,cfg,ledger,environ={'KEY_A':'different-wallet-key','KEY_B':'fake-b'})
    assert ledger.remaining(cfg.pools[0])==50000
    ledger.close()


@pytest.mark.parametrize('url',[None,'file:///private','https://user:secret@example.com','https://example.com:bad'])
async def test_invalid_urls_are_rejected_before_budget_reservation(tmp_path,settings,url):
    _,ledger,executor=runtime(tmp_path,settings,family='jina',unit='tokens',limit=50000)
    with pytest.raises(ProviderError) as error:
        await executor.execute('jina_reader',BoundedJinaReader,'fetch',url)
    assert error.value.kind=='config' and ledger.calls()==[]
    ledger.close()


async def test_reported_usage_over_cap_blocks_wallet_and_is_not_hidden(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,family='jina',unit='tokens',limit=50000)
    class Overrun(BoundedJinaReader):
        async def client(self):
            self._client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(
                200,text='content',headers={'X-Usage-Tokens':'9000'})))
            return self._client
    await executor.execute('jina_reader',Overrun,'fetch','https://example.com')
    assert ledger.calls()[0]['charged_units']==9000_000_000
    assert ledger.calls()[0]['error']=='cost_bound_exceeded'
    assert ledger.remaining(cfg.pools[0])==0
    ledger.close()
