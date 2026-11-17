"""Provider registry: builds all backends from settings, exposes lookups."""

from __future__ import annotations

from pathlib import Path

from ..config.settings import Settings, get_settings
from .base import BaseProvider, FetchProvider, Reranker, SearchProvider
from .crawl4ai import Crawl4AIProvider
from .exa import ExaProvider
from .firecrawl import FirecrawlProvider
from .jina import JinaReaderFetcher, JinaReranker
from .light_http import LightHttpFetcher
from .searxng import SearxngProvider
from .tavily import TavilyProvider
from .you_mcp import YouMcpProvider


class Registry:
    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        self.settings = s
        self.search: dict[str, SearchProvider] = {
            "you_mcp": YouMcpProvider(s),
            "searxng": SearxngProvider(s),
            "exa": ExaProvider(s),
            "tavily": TavilyProvider(s),
        }
        self.fetch: dict[str, FetchProvider] = {
            "light_http": LightHttpFetcher(s),
            "jina_reader": JinaReaderFetcher(s),
            "crawl4ai": Crawl4AIProvider(s),
            "firecrawl": FirecrawlProvider(s),
        }
        self.rerankers: dict[str, Reranker] = {
            "jina_reranker": JinaReranker(s),
        }
        self.credentials = None
        if getattr(s, "accounts_file", "") or getattr(s,"vault_key",""):
            from .key_pool import CredentialExecutor, ManagedProvider, load_accounts
            from ..quota.ledger import AccountLedger
            if getattr(s,"accounts_file",""):
                config = load_accounts(s.accounts_file)
            else:
                from ..quota.accounts import AccountConfig
                # Existing environment keys are represented as unknown-budget
                # legacy accounts, never as freshly granted allowances.
                # Keep legacy You requests units stable for persisted base hashes;
                # bounded You execution requires an explicitly configured USD pool.
                accounts=[];pools=[];keys=[]
                for family,attribute,env,unit in (
                    ("exa","exa_api_key","EXA_API_KEY","usd"),("tavily","tavily_api_key","TAVILY_API_KEY","credits"),
                    ("firecrawl","firecrawl_api_key","FIRECRAWL_API_KEY","credits"),("jina","jina_api_key","JINA_API_KEY","tokens"),("you","ydc_api_key","YDC_API_KEY","requests")):
                    if not getattr(s,attribute,""):
                        continue
                    identity="legacy-"+family
                    accounts.append(dict(id=identity,provider=family,label=family+" 原有账户"))
                    pools.append(dict(id=identity,account_id=identity,unit=unit))
                    keys.append(dict(id=identity,account_id=identity,label="原有 Key",pool_ids=[identity],secret_env=env))
                config=AccountConfig.model_validate(dict(accounts=accounts,pools=pools,keys=keys))
            ledger = AccountLedger(str(Path(s.usage_db_path).with_suffix(".accounts.sqlite3")))
            try:
                self.credentials = CredentialExecutor(s, config, ledger)
                for collection in (self.search, self.fetch, self.rerankers):
                    for name, provider in list(collection.items()):
                        if self.credentials.manages(name):
                            collection[name] = ManagedProvider(provider, self.credentials)
            except BaseException:
                ledger.close()
                raise
        self.all: dict[str, BaseProvider] = {}
        self.all.update(self.search)
        self.all.update(self.fetch)
        self.all.update(self.rerankers)

    def refresh_credentials(self):
        if self.credentials is None:
            return
        from .key_pool import ManagedProvider
        self.credentials.manager.reload()
        for collection in (self.search,self.fetch,self.rerankers):
            for name,provider in list(collection.items()):
                if self.credentials.manages(name) and not getattr(provider,"managed_credentials",False):
                    collection[name]=ManagedProvider(provider,self.credentials)
                    self.all[name]=collection[name]

    def get_search(self, name: str) -> SearchProvider:
        return self.search[name]

    def get_fetch(self, name: str) -> FetchProvider:
        return self.fetch[name]

    def get_reranker(self, name: str = "jina_reranker") -> Reranker:
        return self.rerankers[name]

    async def aclose(self) -> None:
        for p in self.all.values():
            await p.aclose()
        if self.credentials is not None:
            self.credentials.ledger.close()
