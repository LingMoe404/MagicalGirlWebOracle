"""Explicit site constraints and zero-network local reranking."""
from types import SimpleNamespace

import pytest

from magicalgirl_web_oracle.providers.base import ProviderError
from magicalgirl_web_oracle.router.oracle import Oracle
from magicalgirl_web_oracle.router.search_quality import filter_sites,rerank_local,site_constraints
from magicalgirl_web_oracle.quota.usage_db import UsageDB
from conftest import FakeSearchProvider,make_result
from test_key_pool import runtime


@pytest.mark.parametrize('query,allowed',[
    ('site:example.com topic',[0,1]),('site:EXAMPLE.com. topic',[0,1]),
    ('site:*.example.com topic',[0,1]),('site:example.com/docs topic',[0]),
    ('site:example.com OR site:other.com topic',[0,1,3]),
    ('site:example.com -site:sub.example.com topic',[0]),('-site:example.com topic',[2,3]),
    ('"literal site:example.com"',[0,1,2,3]),('site:"example.com" topic',[0,1]),
])
def test_site_matching_domain_boundaries_exclusions_and_paths(query,allowed):
    urls=['https://www.example.com/docs/a','https://sub.example.com/other','https://example.com.evil.test/docs','https://other.com/docs']
    results=[make_result(url,'exa') for url in urls]
    assert filter_sites(results,site_constraints(query))==[results[i] for i in allowed]


@pytest.mark.parametrize('query',['site:https://user:secret@example.com','site:example.com:8080','site:"example.com docs"','site:https://example.com/?q=other','site:','site:"example.com'])
def test_unsupported_site_syntax_is_rejected(query):
    with pytest.raises(ValueError): site_constraints(query)


def test_url_path_that_contains_site_is_not_an_operator():
    assert site_constraints('https://example.com/site:literal.test')==[]


def test_local_query_coverage_prioritizes_relevant_title_without_mutating_sources():
    noise=make_result('https://noise.test','tavily',title='YouTube news',score=0.99)
    relevant=make_result('https://docs.test','exa',title='Jina token budget',score=0.3)
    ranked=rerank_local('Jina token budget',[noise,relevant])
    assert ranked[0].url==relevant.url and ranked[0].provider=='exa'
    assert ranked[0].metadata['reranked_by']=='local'
    assert 'reranked_by' not in relevant.metadata and relevant.score==0.3
    assert 0<=ranked[1].score<=ranked[0].score<=1


def test_chinese_bigrams_and_empty_terms_keep_deterministic_order():
    noise=make_result('https://noise.test','exa',title='新闻',score=0.9)
    relevant=make_result('https://docs.test','tavily',title='免费额度查询',score=0.2)
    assert rerank_local('免费额度',[noise,relevant])[0].url==relevant.url
    assert [r.url for r in rerank_local('site:docs.test',[relevant,noise])]==[noise.url,relevant.url]


async def test_multikey_search_filters_sites_and_never_calls_external_reranker(tmp_path,settings):
    _,ledger,executor=runtime(tmp_path,settings)
    search=FakeSearchProvider([
        make_result('https://youtube.com/noise','searxng',title='YouTube',score=0.99),
        make_result('https://docs.jina.ai/budget','searxng',title='Jina token budget',score=0.2),
        make_result('https://docs.jina.ai/other','searxng',title='Other page',score=0.9),
    ],settings)
    class Never:
        configured=True
        async def rerank(self,*args,**kwargs): raise AssertionError('no external rerank')
    registry=SimpleNamespace(credentials=executor,search={'searxng':search},fetch={},rerankers={'jina_reranker':Never()},all={},refresh_credentials=lambda:None)
    usage=UsageDB(settings.usage_db_path)
    oracle=Oracle(settings=settings,registry=registry,usage=usage)
    response=await oracle.web_search('site:docs.jina.ai Jina token budget',mode='max',provider_override='searxng')
    assert response.results[0].url=='https://docs.jina.ai/budget'
    assert response.dedup_stats=={'raw':3,'deduped':2,'site_filtered':1}
    assert all(result.metadata['reranked_by']=='local' for result in response.results)
    assert any('reranked locally' in note for note in response.notes)
    assert len(search.calls)==1 and ledger.calls()==[]  # no token reservations
    detail=ledger.trace_detail(ledger.traces()[0]['id'])
    assert any(event['details'].get('provider')=='local_rerank' for event in detail['events'])
    assert any(event['details'].get('reason')=='site_filter' for event in detail['events'])
    assert not any(event['details'].get('provider')=='jina_reranker' for event in detail['events'])
    usage.close();ledger.close()


async def test_all_offsite_results_stay_empty_without_extra_requests(tmp_path,settings):
    _,ledger,executor=runtime(tmp_path,settings)
    search=FakeSearchProvider([make_result('https://unrelated.test','searxng')],settings)
    registry=SimpleNamespace(credentials=executor,search={'searxng':search},fetch={},rerankers={},all={},refresh_credentials=lambda:None)
    usage=UsageDB(settings.usage_db_path)
    oracle=Oracle(settings=settings,registry=registry,usage=usage)
    response=await oracle.web_search('site:official.test topic',provider_override='searxng')
    assert response.results==[] and response.dedup_stats['site_filtered']==1
    assert any('constraints were not relaxed' in note for note in response.notes)
    assert len(search.calls)==1
    with pytest.raises(ProviderError):
        await oracle.web_search('site:official.test:8080 topic',provider_override='searxng')
    assert len(search.calls)==1
    usage.close();ledger.close()
