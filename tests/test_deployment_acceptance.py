"""Contracts found during live deployment preflight; uses no real credentials."""
from datetime import datetime,timezone
import json,time

import pytest
import httpx

from magicalgirl_web_oracle.quota.accounts import BillingWindow
from magicalgirl_web_oracle.quota.balances import normalize
from magicalgirl_web_oracle.providers.jina_bounded import BoundedJinaReader
from test_key_pool import runtime


def test_monthly_reset_preserves_exact_supplier_time():
    window=BillingWindow(kind='monthly',reset_day=9,reset_time='13:12:15.001',timezone='UTC')
    start,end=window.bounds(datetime(2026,11,9,13,12,14,tzinfo=timezone.utc))
    assert start==datetime(2026,10,9,13,12,15,1000,tzinfo=timezone.utc)
    assert end==datetime(2026,11,9,13,12,15,1000,tzinfo=timezone.utc)
    start,_=window.bounds(end)
    assert start==end


@pytest.mark.parametrize('value',['25:00:00','12:60:00','10:20'])
def test_invalid_supplier_reset_time_is_rejected(value):
    with pytest.raises(ValueError): BillingWindow(kind='monthly',reset_day=1,reset_time=value)


def test_default_clock_field_upgrade_preserves_confirmed_spend(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,limit=10,initial=2)
    call=ledger.reserve(cfg.keys[0],executor.pools,'tavily','search',1)
    ledger.dispatch(call);ledger.settle(call,1)
    identity=json.loads(ledger.db.execute('SELECT identity FROM pools').fetchone()[0])
    identity['window'].pop('reset_time')
    ledger.db.execute('UPDATE pools SET identity=?',(json.dumps(identity),))
    ledger.register(cfg,executor._secrets)
    assert ledger.remaining(cfg.pools[0])==7
    ledger.close()


def test_jina_trial_excludes_paid_tokens_and_expires():
    raw={'trial_balance':100,'total_balance':10100,'trial_start':'2020-01-01T00:00:00Z','trial_end':'2099-01-01T00:00:00Z'}
    value=normalize('jina',raw)
    assert value['remaining']=='10100' and value['free_remaining']=='100' and value['budget_verified']
    raw['trial_end']='2021-01-01T00:00:00Z'
    assert normalize('jina',raw)['free_remaining']=='0'


def test_firecrawl_total_is_only_a_ceiling_for_confirmed_free_pool(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,shared=True,family='firecrawl',limit=1000,initial=100)
    watermark,held=ledger.begin_balance_sync('a')
    value=normalize('firecrawl',{'remaining_credits':1393,'plan_credits':1000,'billing_period_start':None,'billing_period_end':None})
    ledger.save_balance('a','k-a',value,watermark,held)
    assert ledger.remaining(cfg.pools[0])==900
    assert value['free_remaining'] is None and not value['budget_verified']
    ledger.close()


async def test_jina_wallet_wire_discards_personal_and_payment_fields(settings):
    settings.jina_api_key='fake-private-key'
    provider=BoundedJinaReader(settings)
    def handler(request):
        assert str(request.url)=='https://dash.jina.ai/api/v1/authorization'
        assert request.headers['Authorization']=='Bearer fake-private-key'
        assert not request.url.query
        return httpx.Response(200,json={'email':'private@example.com','payment_method':{'last4':'0000'},
            'wallet':{'trial_balance':10,'regular_balance':100,'total_balance':110,'trial_start':'2020-01-01T00:00:00Z','trial_end':'2099-01-01T00:00:00Z'}})
    provider._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    raw=await provider.fetch_balance()
    assert 'email' not in raw and 'payment_method' not in raw
    assert normalize('jina',raw)['free_remaining']=='10'
    await provider.aclose()


def test_separate_https_admin_does_not_expose_mcp(settings):
    from starlette.testclient import TestClient
    from magicalgirl_web_oracle.admin.tls_server import build_app
    settings.admin_auth_token='test-admin-token'
    with TestClient(build_app(settings),base_url='https://admin.test') as client:
        assert client.get('/healthz').json()['ok']
        assert client.get('/admin/').status_code==200
        assert client.get('/admin/api/overview').status_code==401
        assert client.post('/mcp').status_code==404


@pytest.mark.parametrize('status',[200,202])
async def test_firecrawl_v2_bounded_create_poll_contract(settings,monkeypatch,status):
    from magicalgirl_web_oracle.providers.firecrawl import FirecrawlProvider
    import asyncio
    settings.firecrawl_api_key='fake-fire-key'
    provider=FirecrawlProvider(settings);seen=[]
    def handler(request):
        seen.append(request.method)
        if request.method=='POST':
            body=json.loads(request.content)
            assert body['limit']==1 and body['maxDiscoveryDepth']==1 and 'maxDepth' not in body
            return httpx.Response(status,json={'id':'fake-job'})
        return httpx.Response(200,json={'status':'completed','data':[{'markdown':'page','metadata':{'sourceURL':'https://example.com'}}]})
    async def no_wait(*args): pass
    monkeypatch.setattr(asyncio,'sleep',no_wait)
    provider._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result=await provider.crawl_site('https://example.com',max_pages=1,max_depth=1)
    assert len(result.pages)==1 and seen==['POST','GET']
    await provider.aclose()
