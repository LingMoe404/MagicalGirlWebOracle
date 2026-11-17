"""Official total ceilings restrict, never grant, local free USD budgets."""
from decimal import Decimal
import time

import httpx
import pytest

from magicalgirl_web_oracle.providers.you_search import YouSearchProvider
from magicalgirl_web_oracle.quota.balances import BalanceSync, normalize
from magicalgirl_web_oracle.quota.ledger import BudgetUnavailable,uses_total_ceiling
from magicalgirl_web_oracle.quota.scheduler import BalanceScheduler
from test_key_pool import runtime


def sync_with(executor,cents,counter=None):
    class Provider:
        def __init__(self,settings): pass
        async def fetch_balance(self):
            if counter is not None: counter.append(1)
            if isinstance(cents,Exception): raise cents
            return {'balance_cents':cents}
        async def aclose(self): pass
    executor.balance_sync=BalanceSync(executor,factories={'you':(Provider,'ydc_api_key')})
    return executor.balance_sync


@pytest.mark.parametrize('cents,usd',[('13.5','0.135'),('718924.5','7189.245'),('0.00019','0.000001'),('0.00001','0'),(0,'0')])
def test_cents_conversion_is_exact_and_never_rounded_up(cents,usd):
    value=normalize('you',{'balance_cents':cents,'free_remaining':999,'budget_verified':True})
    assert Decimal(value['remaining'])==Decimal(usd)
    assert value['unit']=='usd' and value['source_unit']=='cents'
    assert value['free_remaining'] is None and not value['budget_verified']
    assert uses_total_ceiling(value)


@pytest.mark.parametrize('cents',[None,True,-1,'NaN','Infinity',{},'not-a-number'])
def test_invalid_total_is_rejected(cents):
    with pytest.raises(Exception): normalize('you',{'balance_cents':cents})


async def test_real_balance_wire_contract_keeps_only_balance(settings):
    settings.ydc_api_key='private-key'
    provider=YouSearchProvider(settings)
    seen=[]
    def handler(request):
        seen.append(request)
        assert request.method=='GET' and str(request.url)=='https://api.you.com/v1/billing/account_balance'
        assert request.headers['X-API-Key']=='private-key'
        return httpx.Response(200,text='{"data":{"id":"private-tenant","attributes":{"balance":13.50019,"email":"private@example.com"}}}')
    provider._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    value=await provider.fetch_balance()
    assert value=={'balance_cents':Decimal('13.50019')}
    assert normalize('you',value)['remaining']=='0.135001'
    assert len(seen)==1
    await provider.aclose()


async def test_total_never_increases_confirmed_free_budget_or_resets_consumption(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,family='you',unit='usd',limit=1,initial='0.25')
    await sync_with(executor,100000).run(['a'])
    assert ledger.remaining(cfg.pools[0])==Decimal('0.75')
    call=ledger.reserve(cfg.keys[0],executor.pools,'you_mcp','search','0.005')
    ledger.dispatch(call);ledger.settle(call,'0.005')
    ledger.db.execute('UPDATE balance_sync SET next_allowed=0')
    await sync_with(executor,200000).run(['a'])
    assert ledger.remaining(cfg.pools[0])==Decimal('0.745')
    assert ledger.balances()['a']['free_remaining'] is None
    assert not any(alert['kind']=='unverified_balance' for alert in executor.alerts.evaluate())
    ledger.close()


async def test_official_total_does_not_unlock_unknown_free_budget(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,family='you',unit='usd',confirmed=False)
    await sync_with(executor,100000).run(['a'])
    assert ledger.remaining(cfg.pools[0])==0
    with pytest.raises(BudgetUnavailable): ledger.reserve(cfg.keys[0],executor.pools,'you_mcp','search','0.005')
    assert any(alert['kind']=='unconfirmed_pool' for alert in executor.alerts.evaluate())
    ledger.close()


async def test_shared_total_ceiling_watermark_and_preexisting_uncertainty(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,family='you',unit='usd',limit=1)
    pending=ledger.reserve(cfg.keys[0],executor.pools,'you_mcp','search','0.005')
    ledger.dispatch(pending)
    class Provider:
        def __init__(self,settings): pass
        async def fetch_balance(self):
            newer=ledger.reserve(cfg.keys[1],executor.pools,'you_mcp','search','0.005')
            ledger.dispatch(newer);ledger.settle(newer,'0.005')
            ledger.settle(pending,'0.005')
            return {'balance_cents':2}
        async def aclose(self): pass
    executor.balance_sync=BalanceSync(executor,factories={'you':(Provider,'ydc_api_key')})
    await executor.balance_sync.run(['a'])
    assert ledger.account_remaining('a',list(cfg.pools))==Decimal('0.01')  # .02 - .005 held - .005 newer
    assert ledger.remaining(cfg.pools[0])==Decimal('0.01')
    ledger.close()


async def test_total_sync_is_deduplicated_stale_and_failure_keep_safe_state(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,family='you',unit='usd',limit=1)
    counter=[]
    await sync_with(executor,1,counter).run(['a','a'])
    assert counter==[1] and ledger.remaining(cfg.pools[0])==Decimal('0.01')
    before=ledger.balances()['a']['observed_at']
    ledger.db.execute('UPDATE balance_sync SET next_allowed=0')
    await sync_with(executor,ValueError('private-response')).run(['a'])
    assert ledger.remaining(cfg.pools[0])==Decimal('0.01')
    assert ledger.balances()['a']['observed_at']==before
    assert 'private-response' not in str(ledger.balances())
    ledger.db.execute('UPDATE balance_snapshots SET expires=0')
    assert ledger.remaining(cfg.pools[0])==0
    assert any(alert['kind']=='stale_balance' for alert in executor.alerts.evaluate())
    ledger.close()


async def test_scheduler_supports_usd_and_leaves_legacy_requests_untouched(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,family='you',unit='usd')
    counter=[];sync_with(executor,1,counter)
    scheduler=BalanceScheduler(executor)
    assert scheduler.due_accounts(time.time())==['a']
    await scheduler.tick()
    assert counter==[1] and scheduler.due_accounts(time.time())==[]
    ledger.close()
    _,ledger,executor=runtime(tmp_path/'legacy',settings,shared=True,family='you',unit='requests')
    sync_with(executor,1,counter)
    assert (await executor.balance_sync.run(['a']))[0]['status']=='unsupported_unit'
    assert BalanceScheduler(executor).due_accounts(time.time())==[]
    assert counter==[1] and ledger.balances()=={}
    ledger.close()


def test_total_policy_cannot_be_reused_to_unlock_other_provider(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True)
    watermark,held=ledger.begin_balance_sync('a')
    snapshot=normalize('you',{'balance_cents':10000})
    snapshot['provider']='firecrawl'
    ledger.save_balance('a','k-a',snapshot,watermark,held)
    assert not uses_total_ceiling(snapshot) and ledger.remaining(cfg.pools[0])==0
    ledger.close()


def test_ledger_rejects_total_ceiling_with_wrong_pool_units(tmp_path,settings):
    _,ledger,_=runtime(tmp_path,settings,shared=True,family='you',unit='requests')
    watermark,held=ledger.begin_balance_sync('a')
    with pytest.raises(ValueError,match='USD-only'):
        ledger.save_balance('a','k-a',normalize('you',{'balance_cents':100}),watermark,held)
    assert not ledger.balance_history(0,time.time()+1)
    ledger.close()
