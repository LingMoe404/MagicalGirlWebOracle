"""Official balance caps, races and schema upgrades, without network calls."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import sqlite3
import time

import pytest

from magicalgirl_web_oracle.quota.balances import BalanceSync, normalize
from magicalgirl_web_oracle.quota.ledger import AccountLedger, BudgetUnavailable
from test_key_pool import runtime, account_config


def official(used=2,limit=10,plan="Researcher"):
    return {"plan":plan,"used":used,"limit":limit,"paygo_used":999,"paygo_limit":9999}


def mock_sync(executor, response, counter=None):
    class Provider:
        def __init__(self,settings): pass
        async def fetch_balance(self):
            if counter is not None: counter.append(1)
            if isinstance(response,Exception): raise response
            return response
        async def aclose(self): pass
    executor.balance_sync=BalanceSync(executor,factories={"tavily":(Provider,"tavily_api_key")})
    return executor.balance_sync


def test_free_tier_is_distinct_from_paygo_and_paid_plan():
    free=normalize("tavily",official())
    assert free["remaining"]=="8" and free["free_remaining"]=="8"
    paid=normalize("tavily",official(plan="Bootstrap"))
    assert paid["remaining"]=="8" and paid["free_remaining"] is None
    assert not paid["budget_verified"]


@pytest.mark.parametrize("value",[-1,"NaN","Infinity",None,True,"0.0000001"])
def test_malformed_balances_are_not_saved(value):
    with pytest.raises(Exception):
        normalize("tavily",official(used=value))


def test_firecrawl_total_is_official_but_not_a_verified_free_allowance():
    snapshot=normalize("firecrawl",{"remaining_credits":1399,"plan_credits":1000,"billing_period_start":"2026-11-01T00:00:00Z","billing_period_end":"2026-12-01T00:00:00Z"})
    assert snapshot["remaining"]=="1399"
    assert snapshot["free_remaining"] is None
    assert not snapshot["budget_verified"]
    assert snapshot["period_end"]==datetime(2026,12,1,tzinfo=timezone.utc).timestamp()


@pytest.mark.asyncio
async def test_one_sync_per_account_shared_by_keys_and_rate_limit(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10)
    counter=[];sync=mock_sync(executor,official(),counter)
    result=await sync.run(["a","a"])
    assert len(result)==1 and result[0]["status"]=="ok" and len(counter)==1
    assert ledger.remaining(cfg.pools[0])==8
    assert (await sync.run())[0]["status"]=="rate_limited" and len(counter)==1
    ledger.close()


@pytest.mark.asyncio
async def test_no_duplicate_subtraction_of_settled_usage(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10,initial=2)
    call=ledger.reserve(cfg.keys[0],executor.pools,"tavily","search",1)
    ledger.dispatch(call);ledger.settle(call,1)
    await mock_sync(executor,official(used=3)).run()
    assert ledger.remaining(cfg.pools[0])==7  # not 7 - initial 2 - settled 1
    newer=ledger.reserve(cfg.keys[1],executor.pools,"tavily","search",2)
    ledger.dispatch(newer);ledger.settle(newer,2)
    assert ledger.remaining(cfg.pools[0])==5
    ledger.close()


@pytest.mark.asyncio
async def test_requests_during_sync_are_subtracted_and_existing_inflight_retained(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10)
    old=ledger.reserve(cfg.keys[0],executor.pools,"tavily","search",2)
    ledger.dispatch(old)
    class Provider:
        def __init__(self,s): pass
        async def fetch_balance(self):
            new=ledger.reserve(cfg.keys[0],executor.pools,"tavily","search",1)
            ledger.dispatch(new);ledger.settle(new,1)
            ledger.settle(old,1)
            return official(used=2)
        async def aclose(self): pass
    sync=BalanceSync(executor,factories={"tavily":(Provider,"tavily_api_key")})
    await sync.run()
    assert ledger.remaining(cfg.pools[0])==5  # official 8 - pre-sync reserved 2 - during-sync 1
    ledger.close()


@pytest.mark.asyncio
async def test_failed_sync_preserves_snapshot_and_stale_cap_blocks_calls(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10)
    await mock_sync(executor,official()).run()
    before=ledger.balances()["a"]
    ledger.db.execute("UPDATE balance_sync SET next_allowed=0")
    result=await mock_sync(executor,ValueError("never-expose-upstream-body")).run()
    after=ledger.balances()["a"]
    assert result[0]["status"]=="failed"
    assert after["observed_at"]==before["observed_at"] and after["remaining"]=="8"
    assert "never-expose" not in str(after)
    assert ledger.remaining(cfg.pools[0])==8
    ledger.db.execute("UPDATE balance_snapshots SET expires=0")
    assert ledger.remaining(cfg.pools[0])==0
    with pytest.raises(BudgetUnavailable):
        ledger.reserve(cfg.keys[0],executor.pools,"tavily","search",1)
    ledger.close()


@pytest.mark.asyncio
async def test_snapshot_cannot_open_unconfirmed_pool_or_enlarge_budget(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=5,confirmed=False)
    await mock_sync(executor,official(used=0,limit=1000)).run()
    assert ledger.remaining(cfg.pools[0])==0
    ledger.close()
    cfg,ledger,executor=runtime(tmp_path/"second",settings,shared=True,limit=5)
    await mock_sync(executor,official(used=0,limit=1000)).run()
    assert ledger.remaining(cfg.pools[0])==5
    ledger.close()


@pytest.mark.asyncio
async def test_account_cap_is_shared_across_multiple_pools(tmp_path,settings):
    from magicalgirl_web_oracle.providers.key_pool import CredentialExecutor
    cfg=account_config(shared=True,limit=5)
    bonus=cfg.pools[0].model_copy(update={"id":"bonus"})
    keys=tuple(k.model_copy(update={"pool_ids":("p-a","bonus")}) for k in cfg.keys)
    cfg=cfg.model_copy(update={"pools":(*cfg.pools,bonus),"keys":keys})
    ledger=AccountLedger(str(tmp_path/"accounts.sqlite3"))
    executor=CredentialExecutor(settings,cfg,ledger,environ={"KEY_A":"fake-a","KEY_B":"fake-b"})
    await mock_sync(executor,official(used=2,limit=5)).run()  # combined cap = 3, not 3 per pool
    with pytest.raises(BudgetUnavailable):
        ledger.reserve(keys[0],executor.pools,"tavily","search",4)
    assert executor.snapshot("tavily").remaining==3
    ledger.reserve(keys[0],executor.pools,"tavily","search",3)
    assert ledger.account_remaining("a",list(cfg.pools))==0
    ledger.close()


@pytest.mark.parametrize('old_version',[1,2,3,4])
def test_legacy_upgrade_has_verified_backup_and_is_idempotent(tmp_path,settings,old_version):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=5,initial=2)
    path=str(ledger.path)
    ledger.db.execute("UPDATE account_schema SET version=?",(old_version,))
    if old_version==1:
        ledger.db.execute("DROP TABLE balance_sync")
        ledger.db.execute("DROP TABLE balance_snapshots")
    else:
        if old_version<4:
            ledger.db.execute('ALTER TABLE balance_sync DROP COLUMN failures')
    if old_version<3:
        ledger.db.execute("DROP TABLE managed_config")
        ledger.db.execute("DROP TABLE managed_secrets")
    if old_version<4:
        ledger.db.execute('DROP TABLE alerts')
        ledger.db.execute('DROP TABLE alert_events')
    ledger.db.execute('DROP TABLE trace_events')
    ledger.db.execute('DROP TABLE request_traces')
    ledger.close()
    upgraded=AccountLedger(path)
    assert upgraded.db.execute("SELECT version FROM account_schema").fetchone()[0]==5
    assert upgraded.remaining(cfg.pools[0])==3
    backups=list(tmp_path.glob('*.before-v5-*.sqlite3'))
    assert len(backups)==1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert backup.execute("SELECT version FROM account_schema").fetchone()[0]==old_version
    upgraded.close()
    again=AccountLedger(path);again.close()
    assert len(list(tmp_path.glob('*.before-v5-*.sqlite3')))==1


def test_future_ledger_version_is_rejected_without_migration(tmp_path):
    path=tmp_path/"future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE account_schema(version INTEGER)")
        db.execute("INSERT INTO account_schema VALUES(99)")
    with pytest.raises(ValueError,match="unsupported"):
        AccountLedger(str(path))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM account_schema").fetchone()[0]==99


def test_unrecognized_upstream_plan_is_not_echoed():
    result=normalize('tavily',official(plan='never-store-this-raw-value'))
    assert result['plan']=='Unrecognized' and result['free_remaining'] is None
    assert 'never-store-this-raw-value' not in str(result)
