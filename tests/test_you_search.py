"""Bounded You.com REST contracts and multi-account routing; no real HTTP."""
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from magicalgirl_web_oracle.admin.app import overview
from magicalgirl_web_oracle.admin.management import KeyCreate,PoolConfirm
from magicalgirl_web_oracle.providers.base import ProviderError
from magicalgirl_web_oracle.providers.key_pool import ManagedProvider
from magicalgirl_web_oracle.providers.you_mcp import YouMcpProvider
from magicalgirl_web_oracle.providers.you_search import YouSearchProvider
from magicalgirl_web_oracle.quota.accounts import BillingWindow
from test_key_pool import runtime
from test_management import managed


async def test_wire_contract_preserves_query_and_sends_only_bounded_options(settings):
    settings.ydc_api_key='private-test-key'
    provider=YouSearchProvider(settings)
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.method=='POST' and str(request.url)=='https://ydc-index.io/v1/search'
        assert request.headers['X-API-Key']=='private-test-key'
        assert json.loads(request.content)=={'query':'中文 site:example.com "原样查询"','count':100}
        return httpx.Response(200,json={'results':{'web':[{'title':'文档','url':'https://example.com/doc?utm_source=x','snippets':['甲','乙']}],
            'news':[{'title':'新闻','url':'https://example.com/news','description':'描述','page_age':'2026-11-04'}]}})
    provider._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    results=await provider.search('中文 site:example.com "原样查询"',max_results=100,mode='max')
    assert len(requests)==1 and len(results)==2
    assert results[0].url=='https://example.com/doc' and results[0].snippet=='甲\n乙'
    assert results[1].published_at=='2026-11-04'
    assert all(result.provider=='you_mcp' and result.metadata['transport']=='you-rest-snippets' for result in results)
    await provider.aclose()


@pytest.mark.parametrize('kwargs',[{'max_results':0},{'max_results':101},{'max_results':True},
    {'max_results':1.5},{'max_results':'10'},{'extraction':{'extraction_mode':'full_page'}},
    {'livecrawl':'all'},{'unknown_addon':True}])
async def test_unsupported_requests_never_reserve_or_reach_http(tmp_path,settings,kwargs):
    _,ledger,executor=runtime(tmp_path,settings,family='you',unit='usd')
    class Never:
        def __init__(self,*args): raise AssertionError('should never create provider')
    with pytest.raises(ProviderError) as failure:
        await executor.execute('you_mcp',Never,'search','query',**kwargs)
    assert failure.value.kind=='config' and ledger.calls()==[]
    assert ledger.trace_detail(ledger.traces()[0]['id'])['status']=='error'
    ledger.close()


async def test_executor_adapter_switches_keys_and_accounts_in_usd(tmp_path,settings,monkeypatch):
    _,ledger,executor=runtime(tmp_path,settings,family='you',unit='usd',limit='0.01')
    requests=[]
    def handler(request):
        requests.append(request.headers['X-API-Key'])
        return httpx.Response(401,text='fake-a private query') if requests[-1]=='fake-a' else httpx.Response(200,json={'results':{'web':[]}})
    async def client(self):
        if self._client is None:
            self._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return self._client
    monkeypatch.setattr(YouSearchProvider,'client',client)
    adapter=ManagedProvider(YouMcpProvider(settings),executor)
    assert adapter.factory is YouSearchProvider
    assert await adapter.search('private query',max_results=100)==[]
    assert requests==['fake-a','fake-b']
    assert ledger.remaining(executor.pools['p-a'])==ledger.remaining(executor.pools['p-b'])==Decimal('0.005')
    detail=ledger.trace_detail(ledger.traces()[0]['id'])
    assert [call['state'] for call in detail['calls']]==['uncertain','settled']
    assert any(e['details'].get('cost_source')=='fixed_schedule' for e in detail['events'])
    assert 'private query' not in json.dumps(detail) and 'fake-a' not in json.dumps(detail)
    ledger.close()


async def test_shared_pool_cannot_spend_last_budget_twice(tmp_path,settings,monkeypatch):
    _,ledger,executor=runtime(tmp_path,settings,family='you',unit='usd',shared=True,limit='0.005')
    count=0
    def handler(request):
        nonlocal count
        count+=1
        return httpx.Response(200,json={'results':{'web':[]}})
    async def client(self):
        self._client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return self._client
    monkeypatch.setattr(YouSearchProvider,'client',client)
    adapter=ManagedProvider(YouMcpProvider(settings),executor)
    await adapter.search('query')
    with pytest.raises(ProviderError): await adapter.search('query')
    assert count==1 and ledger.remaining(executor.pools['p-a'])==0
    ledger.close()


@pytest.mark.parametrize('response',[httpx.Response(200,text='invalid JSON private-key'),
    httpx.Response(200,json={'results':{'web':'invalid'}}),httpx.Response(200,json={'results':{'web':[{'url':None}]}}),
    httpx.Response(500,text='private-key')])
async def test_invalid_or_failed_response_keeps_budget_and_sanitizes_error(tmp_path,settings,response):
    _,ledger,executor=runtime(tmp_path,settings,family='you',unit='usd',shared=True,limit='0.005')
    class MockProvider(YouSearchProvider):
        async def client(self):
            self._client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request:response))
            return self._client
    with pytest.raises(ProviderError) as failure:
        await executor.execute('you_mcp',MockProvider,'search','private query')
    assert 'private-key' not in str(failure.value)
    assert ledger.calls()[0]['state']=='uncertain' and ledger.remaining(executor.pools['p-a'])==0
    ledger.close()


async def test_redirect_is_not_followed(settings):
    settings.ydc_api_key='private-key'
    provider=YouSearchProvider(settings)
    seen=[]
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307,headers={'Location':'https://other.invalid/charge'})
    provider._client=httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=True)
    with pytest.raises(ProviderError): await provider.search('query')
    assert seen==['https://ydc-index.io/v1/search']
    await provider.aclose()


def test_web_created_you_account_uses_unknown_usd_budget_then_explicit_confirmation(managed):
    managed.manager.create(KeyCreate(revision=0,label='You Key',provider='you',account_label='Google-B',secret='private-you-key'))
    pool=managed.config.pools[0]
    assert pool.unit=='usd' and pool.initial_used is None
    assert not pool.free_allowance_confirmed
    managed.manager.confirm_pool(pool.id,PoolConfirm(revision=managed.revision,limit='0.01',initial_used='0.005',
        window=BillingWindow(kind='one_time'),free_allowance_confirmed=True))
    from datetime import datetime,timezone
    data=overview(SimpleNamespace(registry=SimpleNamespace(credentials=managed)),datetime.now(timezone.utc).strftime('%Y-%m'))
    assert data['keys'][0]['status']=='ready' and data['pools'][0]['remaining']=='0.005'
    assert not any(alert['kind']=='unsupported_cost' for alert in data['alerts'])


async def test_old_request_units_remain_blocked_and_reported(tmp_path,settings):
    _,ledger,executor=runtime(tmp_path,settings,family='you',unit='requests')
    alerts=executor.alerts.evaluate()
    assert any(alert['kind']=='unsupported_unit' for alert in alerts)
    from datetime import datetime,timezone
    data=overview(SimpleNamespace(registry=SimpleNamespace(credentials=executor)),datetime.now(timezone.utc).strftime('%Y-%m'))
    assert all(key['status']=='unsupported_unit' for key in data['keys'])
    adapter=ManagedProvider(YouMcpProvider(settings),executor)
    with pytest.raises(ProviderError): await adapter.search('query')
    assert ledger.calls()==[]
    ledger.close()


async def test_authenticated_adapter_never_falls_back_to_keyless(settings):
    settings.ydc_api_key=''
    provider=YouSearchProvider(settings)
    def forbidden(request): raise AssertionError('no request without a key')
    provider._client=httpx.AsyncClient(transport=httpx.MockTransport(forbidden))
    assert not provider.configured
    with pytest.raises(ProviderError) as failure: await provider.search('query')
    assert failure.value.kind=='config'
    assert YouMcpProvider(settings).endpoint=='https://api.you.com/mcp?profile=free'
    await provider.aclose()


async def test_legacy_base_identity_is_not_silently_rewritten(settings,monkeypatch):
    from cryptography.fernet import Fernet
    from magicalgirl_web_oracle.providers.registry import Registry
    settings.vault_key=Fernet.generate_key().decode()
    settings.accounts_file=''
    settings.exa_api_key=settings.tavily_api_key=settings.firecrawl_api_key=settings.jina_api_key=''
    settings.ydc_api_key='fake-legacy-you'
    monkeypatch.setenv('YDC_API_KEY','fake-legacy-you')
    registry=Registry(settings)
    assert registry.credentials.config.pools[0].unit=='requests'
    assert registry.credentials.config.pools[0].window.kind=='unknown'
    assert registry.search['you_mcp'].factory is YouSearchProvider
    await registry.aclose()
