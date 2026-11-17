"""Multi-key Reader only: server-enforced token cap and sanitized diagnostics.

Verified 2026-11-04: https://jina.ai/reader/ (X-Token-Budget),
https://github.com/jina-ai/reader/blob/main/src/api/crawler.ts (X-Usage-Tokens).
"""
from urllib.parse import urlsplit

import httpx

from ..models.schemas import FetchedDocument
from .base import FetchProvider,ProviderError


def reader_budget(options):
    budget=options.get('token_budget',8192)
    if set(options)-{'token_budget'} or type(budget) is not int or not 1<=budget<=32768:
        raise ProviderError('bounded Jina Reader requires token_budget 1..32768 and no add-ons',kind='config')
    return budget


def validate_reader_url(url):
    try:
        if not isinstance(url,str):
            raise ValueError()
        parsed=urlsplit(url)
        valid=parsed.scheme in ('http','https') and parsed.hostname and not parsed.username and not parsed.password
        parsed.port
    except (ValueError,TypeError):
        valid=False
    if not valid:
        raise ProviderError('Jina Reader requires an HTTP URL without embedded credentials',kind='config')


class BoundedJinaReader(FetchProvider):
    name='jina_reader'
    capabilities=['reader','markdown_clean']

    @property
    def configured(self):
        return bool(self.settings.jina_api_key)

    async def fetch_balance(self):
        if not self.configured:
            raise ProviderError('Jina wallet key missing',kind='config')
        try:
            client=await self.client()
            response=await client.get('https://dash.jina.ai/api/v1/authorization',
                headers={'Authorization':f'Bearer {self.settings.jina_api_key}'},timeout=15.0,follow_redirects=False)
        except (httpx.TimeoutException,httpx.TransportError):
            raise ProviderError('Jina wallet transport failed',kind='timeout') from None
        if response.status_code!=200:
            raise ProviderError.map_http_error(response.status_code,'wallet request rejected')
        try:
            data=response.json();wallet=data.get('data',data)['wallet']
            return {key:wallet.get(key) for key in ('trial_balance','trial_start','trial_end','regular_balance','total_balance')}
        except (ValueError,TypeError,KeyError,AttributeError):
            raise ProviderError('Jina wallet response invalid',kind='error') from None

    async def fetch(self,url,**opts):
        budget=reader_budget(opts)
        validate_reader_url(url)
        if not self.configured:
            raise ProviderError('Jina Reader key missing',kind='config')
        try:
            client=await self.client()
            response=await client.post('https://r.jina.ai/',json={'url':url},headers={
                'Authorization':f'Bearer {self.settings.jina_api_key}', 'Accept':'text/plain',
                'X-Return-Format':'markdown','X-Token-Budget':str(budget),
            },timeout=40.0,follow_redirects=False)
        except (httpx.TimeoutException,httpx.TransportError):
            raise ProviderError('Jina Reader transport failed',kind='timeout') from None
        if response.status_code == 422:
            # Jina answers 422 when it cannot process the target page itself
            # (dead link, blocked content) — the Reader service is still up.
            raise ProviderError.target_http(422, response.text, "jina reader upstream")
        if response.status_code!=200:
            raise ProviderError.map_http_error(response.status_code,'upstream Reader request rejected')
        usage=response.headers.get('X-Usage-Tokens')
        self.last_cost_reported=usage is not None
        self.last_cost_tokens=budget
        if usage is not None:
            if not usage.isascii() or not usage.isdecimal() or len(usage)>12:
                raise ProviderError('Jina Reader usage invalid; budget retained',kind='uncertain')
            self.last_cost_tokens=int(usage)
        text=response.text
        title=text.split('\n',1)[0].removeprefix('Title:').strip() if text.startswith('Title:') else None
        return FetchedDocument(url=url,title=title,content=text[:200000],content_type='markdown',provider=self.name,
            metadata={'transport':'jina-bounded-reader','token_budget':budget,'keyless':False})
