"""Request correlation, conservative accounting and diagnostic redaction."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from magicalgirl_web_oracle.models.schemas import FetchedDocument
from magicalgirl_web_oracle.providers.base import ProviderError
from magicalgirl_web_oracle.quota.ledger import AccountLedger
from magicalgirl_web_oracle.quota.tracing import emit, traced
from magicalgirl_web_oracle.router.oracle import Oracle
from magicalgirl_web_oracle.quota.usage_db import UsageDB
from conftest import make_result
from test_key_pool import runtime


class FakeProvider:
    def __init__(self, settings):
        self.settings = settings

    async def search(self, query, **kwargs):
        await asyncio.sleep(0)
        if self.settings.tavily_api_key == 'fake-a':
            raise ProviderError('fake-a private-query https://private.invalid', kind='auth')
        return []

    async def aclose(self):
        pass


async def test_key_fallback_trace_has_costs_reasons_and_no_secret(tmp_path, settings):
    _, ledger, executor = runtime(tmp_path, settings)
    await executor.execute('tavily', FakeProvider, 'search', 'private-query')
    trace = ledger.traces()[0]
    detail = ledger.trace_detail(trace['id'])
    assert trace['status'] == 'success'
    assert trace['providers'] == ['tavily']
    events = detail['events']
    assert [e['details']['key_id'] for e in events if e['event'] == 'candidate'] == ['k-a', 'k-b']
    assert any(e['details'].get('reason') == 'auth' for e in events)
    assert any(e['details'].get('reason') == 'next_key' for e in events)
    assert [c['state'] for c in detail['calls']] == ['uncertain', 'settled']
    assert detail['calls'][1]['allocations'][0] == dict(pool_id='p-b', reserved=1000000, charged=1000000, unit='credits')
    assert ledger.remaining(executor.pools['p-a']) == 4
    for secret in ['fake-a', 'fake-b', 'private-query', 'private.invalid', 'KEY_A']:
        assert secret not in json.dumps(detail)
    ledger.close()


async def test_shared_exhausted_pool_records_skip_without_duplicate_reservation(tmp_path, settings):
    _, ledger, executor = runtime(tmp_path, settings, shared=True, limit=1)
    with pytest.raises(ProviderError):
        await executor.execute('tavily', FakeProvider, 'search', 'secret')
    detail = ledger.trace_detail(ledger.traces()[0]['id'])
    assert detail['status'] == 'error'
    assert len(detail['calls']) == 1
    assert any(e['event'] == 'skipped' and e['details']['reason'] == 'pool_budget_unavailable' for e in detail['events'])
    ledger.close()


async def test_parallel_requests_are_isolated_and_durable(tmp_path, settings):
    _, ledger, executor = runtime(tmp_path, settings, limit=20)
    await asyncio.gather(*(executor.execute('tavily', FakeProvider, 'search', str(i)) for i in range(4)))
    traces = ledger.traces()
    assert len(traces) == 4
    call_sets = [{c['id'] for c in ledger.trace_detail(t['id'])['calls']} for t in traces]
    assert sum(map(len, call_sets)) == len(set.union(*call_sets))
    path = str(ledger.path)
    ledger.close()
    reopened = AccountLedger(path)
    assert len(reopened.traces()) == 4
    assert all(reopened.trace_detail(t['id'])['events'] for t in traces)
    reopened.close()


async def test_cancelled_request_keeps_budget_and_trace_state(tmp_path, settings):
    _, ledger, executor = runtime(tmp_path, settings)
    entered = asyncio.Event()
    class Blocking(FakeProvider):
        async def search(self, *args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(executor.execute('tavily', Blocking, 'search', 'secret'))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    detail = ledger.trace_detail(ledger.traces()[0]['id'])
    assert detail['status'] == 'cancelled'
    assert detail['calls'][0]['state'] == 'uncertain'
    assert ledger.remaining(executor.pools['p-a']) == 4
    ledger.close()


async def test_research_reuses_trace_across_search_and_fetch(tmp_path, settings):
    _, ledger, executor = runtime(tmp_path, settings, limit=20)
    class Facade:
        registry = SimpleNamespace(credentials=executor)
        @traced('web_research')
        async def research(self):
            await executor.execute('tavily', FakeProvider, 'search', 'secret')
            await Oracle.fetch_url(self, 'https://private.invalid', search_content='s'*1200)
    Facade.registry.refresh_credentials = lambda: None
    await Facade().research()
    assert len(ledger.traces()) == 1
    detail = ledger.trace_detail(ledger.traces()[0]['id'])
    assert detail['operation'] == 'web_research'
    assert any(e['event'] == 'reused' for e in detail['events'])
    assert 'private.invalid' not in json.dumps(detail)
    ledger.close()


async def test_trace_limit_unknown_provider_and_reason_allowlists(tmp_path, settings):
    _, ledger, executor = runtime(tmp_path, settings)
    class Facade:
        registry = SimpleNamespace(credentials=executor)
        @traced('web_search')
        async def search(self):
            emit('failed', provider='tavily', reason='SECRET upstream body')
            emit('candidate', provider='SECRET override')
            for _ in range(505):
                emit('candidate', provider='tavily')
    await Facade().search()
    detail = ledger.trace_detail(ledger.traces()[0]['id'])
    assert len(detail['events']) == 500 and detail['dropped_events'] == 6
    assert detail['events'][0]['details']['reason'] == 'error'
    assert 'SECRET' not in json.dumps(detail)
    ledger.close()


async def test_unsupported_cost_is_traced_without_call(tmp_path, settings):
    _, ledger, executor = runtime(tmp_path, settings, family='jina', unit='tokens')
    with pytest.raises(ProviderError):
        await executor.execute('jina_reranker', FakeProvider, 'rerank', 'secret', [])
    detail = ledger.trace_detail(ledger.traces()[0]['id'])
    assert detail['status'] == 'error' and detail['calls'] == []
    assert detail['events'][-1]['details']['reason'] == 'config'
    ledger.close()


async def test_real_research_router_groups_fanout_and_cached_evidence(tmp_path, settings):
    _,ledger,executor=runtime(tmp_path,settings)
    class Success(FakeProvider):
        async def search(self,query,**kwargs):
            return [make_result('https://private.invalid','tavily',content='s'*1200)]
    class Adapter:
        configured=True
        managed_credentials=True
        async def search(self,query,**kwargs):
            return await executor.execute('tavily',Success,'search',query,**kwargs)
    registry=SimpleNamespace(credentials=executor,search={'tavily':Adapter()},fetch={},rerankers={},all={},refresh_credentials=lambda:None)
    usage=UsageDB(settings.usage_db_path)
    oracle=Oracle(settings=settings,registry=registry,usage=usage)
    result=await oracle.web_research('private-query',provider_override='tavily',fetch_top=1)
    assert result.evidence[0].provider=='search_cache'
    assert len(ledger.traces())==1
    detail=ledger.trace_detail(ledger.traces()[0]['id'])
    assert detail['operation']=='web_research'
    assert any(e['event']=='selected' and e['details']['reason']=='manual' for e in detail['events'])
    assert any(e['event']=='reused' for e in detail['events'])
    assert any(e['details'].get('cost_source')=='fixed_schedule' for e in detail['events'])
    assert len(detail['calls'])==1
    assert 'private-query' not in json.dumps(detail) and 'private.invalid' not in json.dumps(detail)
    usage.close();ledger.close()


async def test_search_cancellation_awaits_all_fanout_children(tmp_path, settings):
    _,ledger,executor=runtime(tmp_path,settings)
    entered=[asyncio.Event(),asyncio.Event()]
    cancelled=[]
    class Blocking:
        configured=True
        managed_credentials=True
        def __init__(self,index): self.index=index
        async def search(self,*args,**kwargs):
            entered[self.index].set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(self.index)
    registry=SimpleNamespace(credentials=executor,search={'tavily':Blocking(0),'exa':Blocking(1)},fetch={},rerankers={},all={},refresh_credentials=lambda:None)
    usage=UsageDB(settings.usage_db_path)
    oracle=Oracle(settings=settings,registry=registry,usage=usage)
    oracle.search_router._rank_candidates=lambda *args:['tavily','exa']
    task=asyncio.create_task(oracle.web_search('private-query',mode='max'))
    await asyncio.gather(*(event.wait() for event in entered))
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert sorted(cancelled)==[0,1]
    assert ledger.traces()[0]['status']=='cancelled'
    usage.close();ledger.close()


async def test_crawl_trace_explains_why_retry_is_suppressed(tmp_path,settings):
    _,ledger,executor=runtime(tmp_path,settings,family='firecrawl')
    class Timeout(FakeProvider):
        async def crawl_site(self,*args,**kwargs):
            raise ProviderError('private URL and token',kind='timeout')
    with pytest.raises(ProviderError,match='retry suppressed'):
        await executor.execute('firecrawl',Timeout,'crawl_site','private URL',max_pages=1)
    detail=ledger.trace_detail(ledger.traces()[0]['id'])
    assert len(detail['calls'])==1 and detail['calls'][0]['state']=='uncertain'
    assert detail['events'][-1]['details']['reason']=='retry_suppressed'
    assert 'private URL' not in json.dumps(detail)
    ledger.close()


@pytest.mark.parametrize('reported',[True,False])
async def test_trace_cost_provenance_distinguishes_reported_from_estimate(tmp_path,settings,reported):
    _,ledger,executor=runtime(tmp_path,settings,family='exa',unit='usd')
    class Exa(FakeProvider):
        last_cost_reported=reported
        last_cost_usd=0
        async def search(self,*args,**kwargs): return []
    await executor.execute('exa',Exa,'search','private query',max_results=1)
    detail=ledger.trace_detail(ledger.traces()[0]['id'])
    settlement=next(e for e in detail['events'] if e['event']=='settled')
    assert settlement['details']['cost_source']==('provider_reported' if reported else 'conservative_estimate')
    assert detail['calls'][0]['allocations'][0]['charged']==(0 if reported else 23000)
    ledger.close()
