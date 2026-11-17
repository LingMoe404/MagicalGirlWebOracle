"""Durable scheduling, quiet alerts and lifecycle integration; no live endpoints."""
import asyncio
from contextlib import asynccontextmanager
import time
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from magicalgirl_web_oracle.config.settings import Settings
from magicalgirl_web_oracle.providers.key_pool import CredentialExecutor
from magicalgirl_web_oracle.quota.ledger import AccountLedger,BudgetUnavailable
from magicalgirl_web_oracle.quota.scheduler import BalanceScheduler
from test_key_pool import runtime
from test_balances import mock_sync,official


@pytest.mark.asyncio
async def test_schedule_queries_once_and_recovers_due_time_after_restart(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10)
    calls=[];mock_sync(executor,official(),calls)
    scheduler=BalanceScheduler(executor,interval=300)
    assert (await scheduler.tick())[0]['status']=='ok'
    assert await scheduler.tick()==[] and len(calls)==1
    path=str(ledger.path);ledger.close()
    reopened=AccountLedger(path)
    again=CredentialExecutor(settings,cfg,reopened,environ={'KEY_A':'fake-a','KEY_B':'fake-b'})
    mock_sync(again,official(),calls)
    scheduler=BalanceScheduler(again,interval=300)
    assert await scheduler.tick()==[] and len(calls)==1
    reopened.db.execute('UPDATE balance_sync SET last_attempt=?,next_allowed=0',(time.time()-301,))
    await scheduler.tick()
    assert len(calls)==2
    reopened.close()


@pytest.mark.asyncio
async def test_failure_backoff_and_recovery_are_persistent_and_quiet(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10)
    calls=[];mock_sync(executor,ValueError('never-echo-this-private-body'),calls)
    scheduler=BalanceScheduler(executor,interval=300)
    for count,delay in enumerate((60,120,240,480),start=1):
        ledger.db.execute('UPDATE balance_sync SET next_allowed=0')
        await scheduler.tick()
        state=ledger.balances()['a']
        assert state['failures']==count
        assert state['next_allowed']-state['last_attempt']==pytest.approx(delay,abs=1)
        await scheduler.tick()
        assert len(calls)==count
    events=ledger.alert_history()
    assert sum(e['event']=='activated' and e['alert_id'].endswith('sync_failure') for e in events)==1
    assert 'never-echo' not in str(events)
    mock_sync(executor,official(),calls)
    ledger.db.execute('UPDATE balance_sync SET next_allowed=0')
    await scheduler.tick()
    assert ledger.balances()['a']['failures']==0
    assert not any(a['kind']=='sync_failure' for a in ledger.active_alerts())
    assert any(e['event']=='resolved' for e in ledger.alert_history())
    ledger.close()


def test_global_sync_slots_are_coordinated_by_database(tmp_path):
    ledger=AccountLedger(str(tmp_path/'ledger.sqlite3'))
    other=AccountLedger(str(ledger.path))
    for account in ('a','b','c'):
        ledger.begin_balance_sync(account)
    with pytest.raises(BudgetUnavailable,match='globally busy'):
        other.begin_balance_sync('d')
    ledger.fail_balance_sync('a','cancelled')
    other.begin_balance_sync('d')
    assert ledger.balances()['a']['failures']==0
    other.close();ledger.close()


@pytest.mark.asyncio
async def test_disabled_schedule_does_not_query_and_skips_unsupported_accounts(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True)
    calls=[];mock_sync(executor,official(),calls)
    scheduler=BalanceScheduler(executor,interval=0)
    assert await scheduler.tick()==[] and calls==[]
    cfg2,ledger2,executor2=runtime(tmp_path/'exa',settings,shared=True,family='exa',unit='usd')
    assert BalanceScheduler(executor2).due_accounts(time.time())==[]
    ledger2.close();ledger.close()


@pytest.mark.asyncio
async def test_cancelled_worker_does_not_count_as_provider_failure(tmp_path,settings):
    from magicalgirl_web_oracle.quota.balances import BalanceSync
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True)
    started=asyncio.Event();closed=[]
    class Provider:
        def __init__(self,s): pass
        async def fetch_balance(self):
            started.set()
            await asyncio.Event().wait()
        async def aclose(self): closed.append(True)
    executor.balance_sync=BalanceSync(executor,factories={'tavily':(Provider,'tavily_api_key')})
    scheduler=BalanceScheduler(executor,poll_interval=0.01)
    task=asyncio.create_task(scheduler.run())
    await asyncio.wait_for(started.wait(),1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert not scheduler.status['running'] and closed==[True]
    assert ledger.balances()['a']['status']=='cancelled'
    assert ledger.balances()['a']['failures']==0
    ledger.close()


def test_shared_pool_low_alert_is_not_duplicated_by_key_and_does_not_spam(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10,initial=9)
    first=executor.alerts.evaluate()
    assert len(first)==1 and first[0]['kind']=='low_balance'
    identity=first[0]['id'];version=first[0]['version']
    assert len(ledger.alert_history())==1
    executor.alerts.evaluate();executor.alerts.evaluate()
    assert len(ledger.alert_history())==1
    ledger.dismiss_alert(identity,version)
    executor.alerts.evaluate()
    assert ledger.active_alerts()[0]['dismissed_at'] is not None
    assert len(ledger.alert_history())==2
    ledger.close()


def test_condition_resolves_and_recurrence_resurfaces_dismissed_alert(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10,initial=9)
    alert=executor.alerts.evaluate()[0]
    ledger.dismiss_alert(alert['id'],alert['version'])
    old=executor.alerts.low_ratio
    executor.alerts.low_ratio=0
    assert executor.alerts.evaluate()==[]
    executor.alerts.low_ratio=old
    again=executor.alerts.evaluate()[0]
    assert again['version']==alert['version']+1 and again['dismissed_at'] is None
    with pytest.raises(ValueError,match='changed'):
        ledger.dismiss_alert(again['id'],alert['version'])
    assert [e['event'] for e in reversed(ledger.alert_history())]==['activated','dismissed','resolved','activated']
    ledger.close()


def test_stale_official_cap_is_not_misreported_as_real_depletion(tmp_path,settings):
    from magicalgirl_web_oracle.quota.balances import normalize
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10)
    watermark,held=ledger.begin_balance_sync('a')
    ledger.save_balance('a',cfg.keys[0].id,normalize('tavily',official()),watermark,held)
    ledger.db.execute('UPDATE balance_snapshots SET expires=0')
    alerts=executor.alerts.evaluate()
    assert [a['kind'] for a in alerts]==['stale_balance']
    ledger.close()


def test_invalid_key_alert_recovers_after_replacement_without_echoing_secret(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True)
    ledger.penalize(cfg.keys[0],'auth')
    alerts=executor.alerts.evaluate()
    assert len(alerts)==1 and alerts[0]['kind']=='invalid_key'
    assert 'fake-a' not in str(alerts)
    ledger.register(cfg,{'k-a':'replacement-private-value','k-b':'fake-b'})
    executor._secrets['k-a']='replacement-private-value'
    assert executor.alerts.evaluate()==[]
    assert 'replacement-private-value' not in str(ledger.alert_history())
    ledger.close()


@pytest.mark.parametrize('transport',['streamable-http','sse'])
def test_http_lifespan_preserves_parent_and_stops_worker_before_closing_oracle(tmp_path,settings,monkeypatch,transport):
    from magicalgirl_web_oracle.mcp_server import server
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True)
    settings.accounts_file='configured-for-test'
    settings.balance_sync_interval=0
    events=[]
    @asynccontextmanager
    async def original(app):
        events.append('parent_started')
        yield {'preserved':'yes'}
        events.append('parent_stopped')
    async def probe(request): return JSONResponse({'state':request.state.preserved})
    async def close(): events.append('oracle_closed')
    oracle=SimpleNamespace(registry=SimpleNamespace(credentials=executor),aclose=close)
    monkeypatch.setattr(server,'_settings',settings)
    monkeypatch.setattr(server,'_oracle',oracle)
    monkeypatch.setattr(server.mcp,'streamable_http_app' if transport=='streamable-http' else 'sse_app',lambda:Starlette(routes=[Route('/probe',probe)],lifespan=original))
    with TestClient(server._build_http_app(transport)) as client:
        assert client.get('/probe').json()=={'state':'yes'}
        assert executor.scheduler_status['running']
    assert events==['parent_started','parent_stopped','oracle_closed']
    assert not executor.scheduler_status['running']
    ledger.close()


@pytest.mark.asyncio
async def test_stdio_preserves_async_transport_and_closes_worker(tmp_path,settings,monkeypatch):
    from magicalgirl_web_oracle.mcp_server import server
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True)
    settings.accounts_file='configured-for-test';settings.balance_sync_interval=0
    events=[]
    async def close(): events.append('closed')
    async def stdio():
        await asyncio.sleep(0)
        events.append('stdio')
    monkeypatch.setattr(server,'_settings',settings)
    monkeypatch.setattr(server,'_oracle',SimpleNamespace(registry=SimpleNamespace(credentials=executor),aclose=close))
    monkeypatch.setattr(server.mcp,'run_stdio_async',stdio)
    await server._run_stdio()
    assert events==['stdio','closed'] and not executor.scheduler_status['running']
    ledger.close()


@pytest.mark.parametrize('value',['1','59','481','-1'])
def test_invalid_poll_intervals_rejected(monkeypatch,value):
    monkeypatch.setenv('ORACLE_BALANCE_SYNC_INTERVAL',value)
    with pytest.raises(ValueError): Settings()


@pytest.mark.parametrize('value',['NaN','Infinity','-0.1','1.1'])
def test_invalid_low_quota_threshold_rejected(monkeypatch,value):
    monkeypatch.setenv('ORACLE_LOW_QUOTA_RATIO',value)
    with pytest.raises(ValueError): Settings()


@pytest.mark.asyncio
async def test_status_summary_matches_station_alerts_without_exposing_account_metadata(tmp_path,settings,monkeypatch):
    from click.testing import CliRunner
    from magicalgirl_web_oracle.providers.registry import Registry
    from magicalgirl_web_oracle.router.oracle import Oracle
    from magicalgirl_web_oracle.cli import main as cli_module
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10,initial=9)
    registry=Registry(settings)
    registry.credentials=executor
    oracle=Oracle(settings=settings,registry=registry)
    status=oracle.status()
    assert status.quota_alerts==[{'kind':'low_balance','severity':'warning','count':1}]
    serialized=status.model_dump_json()
    assert 'Google-A' not in serialized and 'fake-a' not in serialized
    monkeypatch.setattr(cli_module,'_run_cli',lambda *args,**kwargs:status)
    result=CliRunner().invoke(cli_module.cli,['status'])
    assert result.exit_code==0 and 'quota_alert kind=low_balance' in result.output
    await oracle.aclose()
