"""Allowlisted request diagnostics: no query, URL, credential or exception text."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from functools import wraps
import time

_current=ContextVar('oracle_request_trace',default=None)
EVENTS={'candidate','skipped','selected','reserved','settled','failed','retry','reused','phase'}
FIELDS={'provider','operation','key_id','call_id','reason','unit','estimate_units','latency_ms','rank','cost_source'}
PROVIDERS={'exa','tavily','firecrawl','jina_reader','jina_reranker','you_mcp','searxng','crawl4ai','light_http','search_cache','fetch_cache','local_rerank'}
REASONS={'priority','manual','ranked','fetch_order','crawl_order','not_configured','unhealthy','caller_skip','disabled','missing_credential','unit_mismatch','budget_or_key_unavailable','auth','quota','rate_limit','timeout','server','config','uncertain','error','permission_or_quota','interrupted_or_invalid_response','next_key','retry_suppressed','search_content','no_backend','cost_bound_exceeded','key_cooldown','key_disabled','credential_changed','official_budget_unavailable','pool_budget_unavailable','policy_limit','wallet_mismatch','local_free','site_filter','cache_hit','cache_bypass','cache_bypass_freshness','scheduling_priority','scheduling_balanced','scheduling_expiry','scheduling_preferred_account','preferred_account_missing'}


def emit(event,**details):
    current=_current.get()
    if current is None:
        return
    if event not in EVENTS or set(details)-FIELDS:
        raise ValueError('invalid trace event schema')
    if 'provider' in details and details['provider'] not in PROVIDERS:
        return  # unknown provider overrides must never become arbitrary log text
    if 'reason' in details and details['reason'] not in REASONS:
        details['reason']='error'
    ledger,trace_id=current
    ledger.append_trace(trace_id,event,details)


def traced(operation):
    """Inherit within a research request, isolate concurrent independent requests."""
    def decorate(function):
        @wraps(function)
        async def wrapped(self,*args,**kwargs):
            executor=self if hasattr(self,'ledger') else getattr(self.registry,'credentials',None)
            ledger=executor.ledger if executor is not None else None
            current=_current.get()
            if ledger is None or (current is not None and current[0] is ledger):
                return await function(self,*args,**kwargs)
            trace_id=ledger.start_trace(operation)
            token=_current.set((ledger,trace_id))
            start=time.monotonic()
            status='error'
            try:
                result=await function(self,*args,**kwargs)
                status='success'
                return result
            except asyncio.CancelledError:
                status='cancelled'
                raise
            finally:
                try:
                    ledger.finish_trace(trace_id,status,int((time.monotonic()-start)*1000))
                finally:
                    _current.reset(token)
        return wrapped
    return decorate
