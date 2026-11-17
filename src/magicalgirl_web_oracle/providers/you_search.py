"""Bounded authenticated You.com search used only by the multi-key executor.

POST snippets-only search: $0.005/call, 1..100 results. No extraction,
livecrawl, MCP tool negotiation, internal retries, or payment operations.
Pricing verified 2026-11-04: https://you.com/docs/administration/billing
Wire contract: https://you.com/docs/api-reference/search/v1-search
"""
from __future__ import annotations

from typing import Any
from decimal import Decimal

import httpx

from ..models.schemas import SearchResult
from ..dedup.canonical import canonicalize_url
from ..langdetect.detector import detect_language
from .base import ProviderError, SearchProvider


def validate_search_options(max_results, options):
    if type(max_results) is not int or not 1 <= max_results <= 100 or options:
        raise ProviderError('You.com multi-key search supports only 1..100 snippet results',kind='config')


class YouSearchProvider(SearchProvider):
    # Preserve the existing routing/source identity, while recording real transport.
    name='you_mcp'
    capabilities=['general','news','fresh']

    @property
    def configured(self):
        return bool(self.settings.ydc_api_key)

    async def fetch_balance(self):
        if not self.configured:
            raise ProviderError('You.com balance key missing',kind='config')
        try:
            client=await self.client()
            response=await client.get('https://api.you.com/v1/billing/account_balance',
                headers={'X-API-Key':self.settings.ydc_api_key},timeout=15.0,follow_redirects=False)
        except (httpx.TimeoutException,httpx.TransportError):
            raise ProviderError('You.com balance transport failed',kind='timeout') from None
        if response.status_code!=200:
            raise ProviderError.map_http_error(response.status_code,'upstream balance request rejected')
        try:
            data=response.json(parse_float=Decimal)
            return {'balance_cents':data['data']['attributes']['balance']}
        except (ValueError,TypeError,KeyError):
            raise ProviderError('You.com balance response invalid',kind='error') from None

    async def search(self,query: str,*,max_results: int=10,mode: str='auto',**opts: Any) -> list[SearchResult]:
        validate_search_options(max_results,opts)
        if not self.configured:
            raise ProviderError('You.com authenticated search key missing',kind='config')
        try:
            client=await self.client()
            response=await client.post(
                'https://ydc-index.io/v1/search',
                headers={'X-API-Key':self.settings.ydc_api_key},
                json={'query':query,'count':max_results},
                timeout=25.0,follow_redirects=False,
            )
        except (httpx.TimeoutException,httpx.TransportError):
            raise ProviderError('You.com search transport failed',kind='timeout') from None
        if response.status_code!=200:
            # Error bodies can echo keys or queries; never propagate them.
            raise ProviderError.map_http_error(response.status_code,'upstream request rejected')
        try:
            data=response.json()
            if not isinstance(data,dict) or not isinstance(data.get('results'),dict):
                raise ValueError()
            web=data['results'].get('web',[])
            news=data['results'].get('news',[])
            if not isinstance(web,list) or not isinstance(news,list):
                raise ValueError()
            out=[]
            # A successful malformed response must remain uncertain, not look free.
            for item in [*web,*news][:max_results]:
                if not isinstance(item,dict) or not isinstance(item.get('url'),str) or not item['url'].startswith(('https://','http://')):
                    raise ValueError()
                snippets=item.get('snippets',[])
                if not isinstance(snippets,list) or any(not isinstance(text,str) for text in snippets):
                    raise ValueError()
                description=item.get('description','')
                title=item.get('title','')
                if not isinstance(description,str) or not isinstance(title,str):
                    raise ValueError()
                snippet=description or '\n'.join(snippets)
                published=item.get('page_age')
                result=SearchResult(title=title.strip(),url=canonicalize_url(item['url']),snippet=snippet.strip(),
                    provider=self.name,score=0.5,language=detect_language(title+snippet),
                    published_at=str(published) if published is not None else None)
                result.metadata={'transport':'you-rest-snippets','cost_source':'fixed_schedule'}
                out.append(result)
            return out
        except (ValueError,TypeError,KeyError,AttributeError):
            raise ProviderError('You.com search response invalid; budget retained',kind='uncertain') from None
