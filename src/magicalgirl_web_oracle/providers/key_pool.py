"""Opt-in credentials executor with one immutable provider per invocation."""
from __future__ import annotations

from copy import copy
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import time
import threading

import yaml

from ..models.schemas import QuotaSnapshot, QuotaType
from ..quota.accounts import AccountConfig
from ..quota.tracing import emit, traced
from ..quota.ledger import units
from ..quota.ledger import AccountLedger, BudgetUnavailable
from .base import BaseProvider, ProviderError

FAMILIES = {"you_mcp": "you", "jina_reader": "jina", "jina_reranker": "jina"}
SECRET_ATTRIBUTES = {"exa": "exa_api_key", "tavily": "tavily_api_key", "firecrawl": "firecrawl_api_key", "jina": "jina_api_key", "you": "ydc_api_key"}


def load_accounts(path: str) -> AccountConfig:
    try:
        return AccountConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
    except Exception:
        # YAML/Pydantic exceptions may contain secret input from a mistaken file.
        raise ValueError("invalid account configuration; check the secret-free schema") from None


class CredentialExecutor:
    def __init__(self, settings, config: AccountConfig, ledger: AccountLedger, *, environ=None):
        self.settings = settings
        self.config = config
        self.ledger = ledger
        self.accounts = {a.id: a for a in config.accounts}
        self.pools = {p.id: p for p in config.pools}
        source = os.environ if environ is None else environ
        self._secrets = {k.id: source.get(k.secret_env, "").strip() for k in config.keys}
        self._config_lock=threading.RLock()
        self.revision=0
        from ..admin.management import KeyManager
        self.manager=KeyManager(self,getattr(settings,"vault_key",""))
        ledger.register(self.config, self._secrets)
        from ..quota.balances import BalanceSync
        self.balance_sync=BalanceSync(self)
        from ..quota.alerts import AlertEngine
        self.alerts=AlertEngine(self,low_ratio=getattr(settings,'low_quota_ratio',0.1))

    def apply_config(self,config,secrets_by_key,revision):
        self.config=config
        self.accounts={account.id:account for account in config.accounts}
        self.pools={pool.id:pool for pool in config.pools}
        self._secrets=secrets_by_key
        self.revision=revision

    def keys_for(self, name):
        family = FAMILIES.get(name, name)
        return sorted((k for k in self.config.keys if self.accounts[k.account_id].provider == family), key=lambda k: (k.priority,k.id))

    def scheduling_info(self):
        strategy = getattr(self.settings, "scheduling_strategy", "priority") or "priority"
        preferred = getattr(self.settings, "preferred_account", "") or None
        explanations = {
            "priority": ("按 Key 优先级顺序尝试，保持既有行为", "scheduling.explanation.priority"),
            "balanced": ("优先使用剩余免费额度比例更高的账户，均衡消耗", "scheduling.explanation.balanced"),
            "expiry": ("优先使用最早重置的已确认免费额度，减少到期浪费", "scheduling.explanation.expiry"),
            "preferred_account": ("优先使用指定账户的 Key，失败后回退到其他 Key", "scheduling.explanation.preferred_account"),
        }
        explanation, explanation_key = explanations.get(strategy, explanations["priority"])
        return {"strategy": strategy, "preferred_account": preferred,
                "explanation": explanation, "explanation_key": explanation_key}

    def _order_candidates(self, name, candidates, pools):
        strategy = getattr(self.settings, "scheduling_strategy", "priority") or "priority"
        preferred = getattr(self.settings, "preferred_account", "") or ""
        if strategy == "priority" or len(candidates) < 2:
            return candidates
        if strategy == "preferred_account":
            if not any(key.account_id == preferred for key in candidates):
                emit("phase",operation=name,reason="preferred_account_missing")
                return candidates
            return sorted(candidates, key=lambda k: (k.account_id != preferred, k.priority, k.id))
        if strategy == "balanced":
            def ratio(key):
                known = [pools[p] for p in key.pool_ids
                         if p in pools and pools[p].limit is not None]
                if not known:
                    return -1.0
                remaining = 0.0
                limit = 0.0
                for pool in known:
                    value = self.ledger.remaining(pool)
                    if value is None:
                        remaining += float(pool.limit)
                    else:
                        remaining += float(value)
                    limit += float(pool.limit)
                return remaining / limit if limit > 0 else -1.0
            return sorted(candidates, key=lambda k: (-ratio(k), k.priority, k.id))
        if strategy == "expiry":
            def next_reset(key):
                moments = []
                for pool_id in key.pool_ids:
                    pool = pools.get(pool_id)
                    if pool is None or pool.limit is None:
                        continue
                    try:
                        _, end = pool.window.bounds(datetime.now(timezone.utc))
                    except (ValueError, OverflowError):
                        continue
                    if end is not None:
                        moments.append(end.timestamp())
                return min(moments) if moments else float("inf")
            return sorted(candidates, key=lambda k: (next_reset(k), k.priority, k.id))
        return candidates

    def manages(self, name):
        return any(a.provider == FAMILIES.get(name,name) for a in self.config.accounts)

    def configured(self, name):
        return any(k.enabled and self._secrets[k.id] for k in self.keys_for(name))

    def estimate(self, name, operation, kwargs):
        """Only bounded operations with a verified quota unit are enabled."""
        if name=="jina_reader" and operation=="fetch":
            from .jina_bounded import reader_budget
            return "tokens", Decimal(reader_budget(kwargs))
        if name == "you_mcp" and operation == "search":
            from .you_search import validate_search_options
            validate_search_options(kwargs.get("max_results",10),{key:value for key,value in kwargs.items() if key not in ("max_results","mode")})
            return "usd", Decimal("0.005")
        if name == "tavily" and operation == "search":
            return "credits", Decimal(2 if kwargs.get("mode") in ("high","max") else 1)
        if name == "exa" and operation == "search":
            count = kwargs.get("max_results",10)
            kind = kwargs.get("exa_type")
            if not isinstance(count,int) or not 1 <= count <= 100 or kind not in (None,"fast","auto","deep-lite","deep"):
                raise ProviderError("unsupported Exa request bounds",kind="config")
            # Conservative bound covers the supported search types, extra
            # results and inline contents. Future add-ons require a new bound.
            return "usd", Decimal("0.02") + Decimal(count)*Decimal("0.003")
        if name == "firecrawl" and operation in ("fetch","crawl_site"):
            count = 1 if operation == "fetch" else kwargs.get("max_pages",10)
            if type(count) is not int or not 1 <= count <= 1000:
                raise ProviderError("invalid crawl page budget",kind="config")
            if operation=='crawl_site':
                depth=kwargs.get('max_depth',2)
                if set(kwargs)-{'max_pages','max_depth'} or type(depth) is not int or not 0<=depth<=10:
                    raise ProviderError('unsupported crawl depth or options',kind='config')
            return "credits", Decimal(count)
        raise ProviderError(f"{name}: multi-key cost bound is not verified; call skipped",kind="config")

    @traced("provider_call")
    async def execute(self, name, factory, operation, *args, **kwargs):
        emit("phase",provider=name,operation=operation)
        try:
            if name=='jina_reader' and operation=='fetch':
                from .jina_bounded import validate_reader_url
                validate_reader_url(args[0] if args else None)
            unit, estimate = self.estimate(name,operation,kwargs)
        except ProviderError:
            emit("failed",provider=name,operation=operation,reason="config")
            raise
        with self._config_lock:
            self.manager.reload()
            candidates=self.keys_for(name)
            pools=self.pools.copy()
            key_secrets=self._secrets.copy()
        strategy = getattr(self.settings, "scheduling_strategy", "priority") or "priority"
        candidate_reason = "priority" if strategy == "priority" else f"scheduling_{strategy}"
        if strategy != "priority":
            emit("phase",operation=operation,reason=candidate_reason)
        candidates = self._order_candidates(name,candidates,pools)
        attempted = False
        health_relevant_failure = False
        for rank,key in enumerate(candidates,1):
            emit('candidate',provider=name,operation=operation,key_id=key.id,rank=rank,reason=candidate_reason)
        for key in candidates:
            if name in ("jina_reader","jina_reranker") and sum(item.account_id==key.account_id for item in self.config.keys)!=1:
                emit("skipped",provider=name,key_id=key.id,reason="wallet_mismatch")
                continue
            if not key.enabled or not key_secrets[key.id]:
                emit('skipped',provider=name,key_id=key.id,reason='disabled' if not key.enabled else 'missing_credential')
                continue
            if any(pools[p].unit != unit or pools[p].window.kind == "unlimited" for p in key.pool_ids):
                emit("skipped",provider=name,key_id=key.id,reason="unit_mismatch")
                continue
            family=FAMILIES.get(name,name)
            fingerprint=self.ledger.fingerprint(family,key_secrets[key.id])
            try:
                call_id = self.ledger.reserve(key,pools,name,operation,estimate,fingerprint=fingerprint)
            except BudgetUnavailable as exc:
                emit("skipped",provider=name,key_id=key.id,reason=exc.reason)
                continue
            emit("reserved",provider=name,key_id=key.id,call_id=call_id,unit=unit,estimate_units=units(estimate))
            attempted = True
            snapshot = copy(self.settings)
            family = FAMILIES.get(name,name)
            setattr(snapshot,SECRET_ATTRIBUTES[family],key_secrets[key.id])
            # In particular, configuring a Jina account must never silently
            # switch to its keyless mode after a credential failure.
            snapshot.jina_keyless_fallback = False
            provider = None
            dispatched = False
            started = time.monotonic()
            try:
                provider = factory(snapshot)
                self.ledger.dispatch(call_id)
                dispatched = True
                result = await getattr(provider,operation)(*args,**kwargs)
                actual = estimate
                cost_source="fixed_schedule" if name in ("tavily","you_mcp") else "conservative_estimate"
                if name=="jina_reader" and getattr(provider,"last_cost_reported",False):
                    actual=Decimal(provider.last_cost_tokens)
                    cost_source="provider_reported"
                elif name == "exa" and getattr(provider,"last_cost_reported",False):
                    actual = Decimal(str(provider.last_cost_usd))
                    cost_source="provider_reported"
                elif name == "firecrawl" and operation == "fetch":
                    actual = Decimal(str(result.metadata.get("credits_used",estimate)))
                    if "credits_used" in result.metadata:
                        cost_source="provider_reported"
                elif name == "firecrawl" and operation == "crawl_site" and not result.pages:
                    raise ProviderError("crawl completion was not confirmed",kind="uncertain")
                self.ledger.settle(call_id,actual,latency_ms=int((time.monotonic()-started)*1000))
                emit("settled",provider=name,key_id=key.id,call_id=call_id,cost_source=cost_source,reason="cost_bound_exceeded" if actual>estimate else "priority",latency_ms=int((time.monotonic()-started)*1000))
                return result
            except ProviderError as exc:
                # Do not persist or echo upstream response bodies: providers
                # may return credential material in diagnostic responses.
                kind = exc.kind if exc.kind in ("auth","quota","rate_limit","timeout","server","config","uncertain","target") else "error"
                if exc.status_code == 403:
                    kind = "permission_or_quota"
                if dispatched:
                    self.ledger.uncertain(call_id,kind,latency_ms=int((time.monotonic()-started)*1000))
                else:
                    self.ledger.release(call_id)
                if exc.affects_health:
                    # Target-level failures (dead URL, anti-bot page) must not
                    # put the credential into cooldown or fail the provider.
                    health_relevant_failure = True
                    self.ledger.penalize(key,kind,fingerprint=fingerprint)
                emit("failed",provider=name,key_id=key.id,call_id=call_id,reason=kind)
                # An asynchronous create request may already have succeeded.
                if operation == "crawl_site":
                    emit("retry",provider=name,key_id=key.id,reason="retry_suppressed")
                    raise ProviderError("crawl outcome uncertain; automatic retry suppressed",kind="uncertain",
                                        affects_health=exc.affects_health) from None
                emit("retry",provider=name,key_id=key.id,reason="next_key")
                continue
            except BaseException as exc:
                if dispatched:
                    self.ledger.uncertain(call_id,"interrupted_or_invalid_response",latency_ms=int((time.monotonic()-started)*1000))
                else:
                    self.ledger.release(call_id)
                emit("failed",provider=name,key_id=key.id,call_id=call_id,reason="interrupted_or_invalid_response")
                # Preserve cancellations; malformed responses also must not
                # be retried as though the first request were free.
                if isinstance(exc,(asyncio.CancelledError,KeyboardInterrupt,SystemExit)):
                    raise
                raise ProviderError(f"{name}: invalid provider response; budget retained",kind="uncertain") from None
            finally:
                if provider is not None:
                    with suppress(Exception):
                        await provider.aclose()
        reason = "all eligible keys failed" if attempted else "no key has confirmed free budget"
        raise ProviderError(f"{name}: {reason}",kind="keys_unavailable",
                            affects_health=health_relevant_failure if attempted else True)

    def snapshot(self, name):
        pool_ids = {p for k in self.keys_for(name) for p in k.pool_ids}
        pools = [self.pools[p] for p in pool_ids]
        if len({p.unit for p in pools})>1:
            return QuotaSnapshot(provider=name,quota_type=QuotaType.UNKNOWN,unit="mixed",remaining=None,renewing=False)
        known = [p for p in pools if p.free_allowance_confirmed and p.limit is not None and p.initial_used is not None]
        values = [self.ledger.account_remaining(account,[p for p in known if p.account_id==account]) for account in {p.account_id for p in known}]
        limit = sum((p.limit for p in known),Decimal(0))
        remaining = sum((v for v in values if v is not None),Decimal(0))
        return QuotaSnapshot(provider=name,quota_type=QuotaType.UNKNOWN,unit=pools[0].unit if pools else "credits",
                             limit=float(limit) if len(known)==len(pools) and pools else None,
                             used=float(limit-remaining),remaining=float(remaining),renewing=False)


class ManagedProvider(BaseProvider):
    managed_credentials = True

    def __init__(self, original, executor):
        super().__init__(original.settings)
        self.name, self.kind, self.capabilities = original.name, original.kind, original.capabilities
        if self.name=="jina_reader":
            from .jina_bounded import BoundedJinaReader
            self.factory=BoundedJinaReader
        elif self.name=="you_mcp":
            from .you_search import YouSearchProvider
            self.factory=YouSearchProvider
        else:
            self.factory = type(original)
        self.executor = executor

    @property
    def configured(self):
        return self.executor.configured(self.name)

    def quota_snapshot(self):
        return self.executor.snapshot(self.name)

    async def search(self, query, **kwargs):
        return await self.executor.execute(self.name,self.factory,"search",query,**kwargs)

    async def fetch(self, url, **kwargs):
        return await self.executor.execute(self.name,self.factory,"fetch",url,**kwargs)

    async def rerank(self, query, results, **kwargs):
        return await self.executor.execute(self.name,self.factory,"rerank",query,results,**kwargs)

    async def crawl_site(self, url, **kwargs):
        return await self.executor.execute(self.name,self.factory,"crawl_site",url,**kwargs)
