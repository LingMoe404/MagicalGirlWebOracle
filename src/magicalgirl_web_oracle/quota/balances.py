"""Account-level official balance synchronization; never stores upstream bodies."""
from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from copy import copy
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

from ..providers.you_search import YouSearchProvider
from ..providers.jina_bounded import BoundedJinaReader
from ..providers.tavily import TavilyProvider
from ..providers.firecrawl import FirecrawlProvider
from .ledger import BudgetUnavailable, SCALE, units


def amount(value) -> Decimal:
    if value is None or isinstance(value,bool):
        raise ValueError("missing numeric balance")
    return Decimal(units(value))/SCALE


def timestamp(value):
    if value is None:
        return None
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("billing period must include a timezone")
    return parsed.timestamp()


def normalize(provider: str, value: dict) -> dict:
    if provider=='jina':
        total,trial=amount(value.get('total_balance')),amount(value.get('trial_balance'))
        start,end=timestamp(value.get('trial_start')),timestamp(value.get('trial_end'))
        if end is None or start is None or start>=end or trial>total:
            raise ValueError('invalid trial wallet bounds')
        free=trial if start<=time.time()<end else Decimal(0)
        return {'provider':'jina','unit':'tokens','remaining':str(total),'limit':None,'plan':'Free trial',
                'free_remaining':str(free),'budget_verified':True,'period_start':start,'period_end':end}
    if provider=='you':
        value=value.get('balance_cents')
        if value is None or isinstance(value,bool):
            raise ValueError('invalid You balance')
        cents=Decimal(str(value))
        if not cents.is_finite() or cents<0:
            raise ValueError('invalid You balance')
        # Round down to the ledger's USD micro-unit; never round budget up.
        remaining=amount((cents/100).quantize(Decimal('0.000001'),rounding=ROUND_DOWN))
        return {'provider':'you','unit':'usd','source_unit':'cents','remaining':str(remaining),'limit':None,
                'plan':None,'free_remaining':None,'budget_verified':False,'cap_policy':'total_ceiling',
                'period_start':None,'period_end':None}
    if provider=="tavily":
        used,limit=amount(value.get("used")),amount(value.get("limit"))
        plan=value.get("plan")
        if not isinstance(plan,str) or len(plan)>80:
            raise ValueError("invalid plan")
        known_plans={name.casefold():name for name in ("Researcher","Free","Project","Bootstrap","Startup","Growth","Enterprise","Pay as you go")}
        plan=known_plans.get(plan.strip().casefold(),"Unrecognized")
        remaining=max(Decimal(0),limit-used)
        # A paid monthly plan must not be mistaken for the free tier. PAYGO
        # balances are never included in this allowance.
        free=remaining if plan.strip().casefold() in ("researcher","free") else None
        return {"provider":provider,"unit":"credits","plan":plan,"remaining":str(remaining),
                "limit":str(limit),"free_remaining":str(free) if free is not None else None,
                "budget_verified":free is not None,"period_start":None,"period_end":None}
    if provider=="firecrawl":
        remaining=amount(value.get("remaining_credits"))
        limit=amount(value.get("plan_credits"))
        start,end=timestamp(value.get("billing_period_start")),timestamp(value.get("billing_period_end"))
        if start is not None and end is not None and start>=end:
            raise ValueError("invalid billing period")
        # The team endpoint does not distinguish free, gift and purchased
        # credits. Display the official total, but never infer a free budget.
        return {"provider":provider,"unit":"credits","plan":None,"remaining":str(remaining),
                "limit":str(limit),"free_remaining":None,"budget_verified":False,"cap_policy":"total_ceiling",
                "period_start":start,"period_end":end}
    raise ValueError("unsupported official balance provider")


class BalanceSync:
    def __init__(self, executor, *, factories=None):
        self.executor=executor
        self.factories=factories or {"tavily":(TavilyProvider,"tavily_api_key"),"firecrawl":(FirecrawlProvider,"firecrawl_api_key"),"you":(YouSearchProvider,"ydc_api_key"),"jina":(BoundedJinaReader,"jina_api_key")}
        self._parallel=asyncio.Semaphore(3)

    async def account(self, account_id: str) -> dict:
        executor=self.executor
        account=executor.accounts.get(account_id)
        if account is None:
            raise ValueError("unknown account")
        if account.provider not in self.factories:
            return {"account_id":account_id,"status":"unsupported"}
        if account.provider=="you" and any(pool.account_id==account_id and pool.unit!="usd" for pool in executor.config.pools):
            return {"account_id":account_id,"status":"unsupported_unit"}
        if account.provider=="jina" and sum(key.account_id==account_id for key in executor.config.keys)!=1:
            return {"account_id":account_id,"status":"wallet_mismatch"}
        states=executor.ledger.key_states()
        candidates=[key for key in executor.keys_for(account.provider) if key.enabled and executor._secrets[key.id] and not states.get(key.id,{}).get("disabled")]
        if not candidates:
            return {"account_id":account_id,"status":"missing_credentials"}
        key=next((key for key in candidates if key.account_id==account_id),None)
        if key is None:
            return {"account_id":account_id,"status":"missing_credentials"}
        async with self._parallel:
            try:
                watermark,held=executor.ledger.begin_balance_sync(account_id)
            except BudgetUnavailable:
                return {"account_id":account_id,"status":"rate_limited"}
            factory,attribute=self.factories[account.provider]
            settings=copy(executor.settings)
            setattr(settings,attribute,executor._secrets[key.id])
            provider=None
            try:
                provider=factory(settings)
                raw=await asyncio.wait_for(provider.fetch_balance(),timeout=20)
                if raw is None:
                    raise ValueError("official usage unavailable")
                snapshot=normalize(account.provider,raw)
                executor.ledger.save_balance(account_id,key.id,snapshot,watermark,held)
                return {"account_id":account_id,"status":"ok","budget_verified":snapshot["budget_verified"]}
            except asyncio.CancelledError:
                executor.ledger.fail_balance_sync(account_id,"cancelled")
                raise
            except Exception:
                executor.ledger.fail_balance_sync(account_id,"official_balance_unavailable")
                return {"account_id":account_id,"status":"failed"}
            finally:
                if provider is not None:
                    with suppress(Exception):
                        await provider.aclose()

    async def run(self, account_ids: list[str] | None = None) -> list[dict]:
        self.executor.manager.reload()
        ids=list(dict.fromkeys(account_ids if account_ids is not None else self.executor.accounts))
        if len(ids)>50 or any(account not in self.executor.accounts for account in ids):
            raise ValueError("invalid account selection")
        return list(await asyncio.gather(*(self.account(account) for account in ids)))
