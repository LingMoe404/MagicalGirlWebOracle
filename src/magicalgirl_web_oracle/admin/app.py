"""Self-contained admin surface; bearer auth stays in browser memory only."""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
import re
import secrets
import time

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..quota.ledger import uses_total_ceiling

STATIC=Path(__file__).parent/"static"


def month_bounds(month: str):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}",month):
        raise ValueError("invalid month")
    start=datetime.strptime(month,"%Y-%m").replace(tzinfo=timezone.utc)
    end=datetime(start.year+(start.month==12),start.month%12+1,1,tzinfo=timezone.utc)
    return start.timestamp(),end.timestamp()


def overview(oracle, month):
    start,end=month_bounds(month)
    executor=getattr(oracle.registry,"credentials",None)
    cache_stats = oracle.cache_stats() if hasattr(oracle,"cache_stats") else None
    payload={"month":month,"timezone":"UTC","generated_at":time.time(),"accounts":[],"pools":[],"keys":[],"calls":[],"traces":[],"balances":{},"balance_history":[],"alerts":[],"alert_history":[],"scheduler":None,"management_enabled":False,"revision":0,"mode":"multi_key" if executor else "legacy","scheduling":None,"cache":cache_stats}
    if executor is None:
        for provider in oracle.status().providers:
            if not provider.configured or provider.provider in ("searxng","crawl4ai","light_http"):
                continue
            name=provider.provider
            # Existing provider totals have no per-key historical attribution.
            # Do not invent monthly key consumption from those totals.
            payload["accounts"].append({"id":name,"label":"现有单 Key","owner_label":"","provider":name})
            payload["keys"].append({"id":name,"account_id":name,"label":name,"pool_ids":[],"enabled":True,"configured":True,"status":"legacy","calls":None,"consumed":None,"unit":provider.quota.unit if provider.quota else "credits"})
        return payload
    ledger=executor.ledger
    executor.manager.reload()
    payload["management_enabled"]=executor.manager.enabled
    payload["revision"]=executor.revision
    payload['alerts']=executor.alerts.evaluate()
    payload['alert_history']=ledger.alert_history()
    payload['scheduler']=getattr(executor,'scheduler_status',{'enabled':getattr(executor.settings,'balance_sync_interval',300)>0,'running':False,'interval_seconds':getattr(executor.settings,'balance_sync_interval',300),'last_tick':None,'last_error':None})
    payload['scheduling']=executor.scheduling_info() if hasattr(executor,'scheduling_info') else None
    payload["balances"]=ledger.balances()
    payload["balance_history"]=ledger.balance_history(start,end)
    payload["traces"]=ledger.traces(start=start,end=end)
    stats=ledger.monthly_stats(start,end)
    states=ledger.key_states()
    payload["accounts"]=[a.model_dump() for a in executor.config.accounts]
    for pool in executor.config.pools:
        known=pool.free_allowance_confirmed and pool.initial_used is not None and pool.window.kind!="unknown"
        remaining=ledger.remaining(pool) if known else None
        _,reset=pool.window.bounds(datetime.now(timezone.utc))
        balance=payload["balances"].get(pool.account_id,{})
        payload["pools"].append({"id":pool.id,"account_id":pool.account_id,"unit":pool.unit,
            "limit":str(pool.limit) if known and pool.limit is not None else None,
            "remaining":str(remaining) if remaining is not None else None,"source":"estimated" if known else "unknown",
            "reset_at":reset.timestamp() if reset else None,"billing_timezone":pool.window.timezone,"window":pool.window.kind,
            "official_remaining":balance.get("remaining"),"official_observed_at":balance.get("observed_at"),"official_stale":balance.get("stale",False),
            "sync_status":balance.get("status"),"official_budget_verified":balance.get("budget_verified",False),"official_total_ceiling":uses_total_ceiling(balance)})
        payload["pools"][-1].update(configured_limit=str(pool.limit) if pool.limit is not None else None,initial_used=str(pool.initial_used) if pool.initial_used is not None else None,reset_day=pool.window.reset_day)
        payload['pools'][-1]['forecast']=ledger.forecast(pool)
    for key in executor.config.keys:
        state=states.get(key.id,{})
        configured=bool(executor._secrets[key.id])
        pools=[executor.pools[p] for p in key.pool_ids]
        available=any(p.free_allowance_confirmed and p.initial_used is not None and p.window.kind!="unknown" and (ledger.remaining(p) is None or ledger.remaining(p)>0) for p in pools)
        confirmed=any(p.free_allowance_confirmed and p.initial_used is not None and p.window.kind!="unknown" for p in pools)
        family=executor.accounts[key.account_id].provider
        status=("disabled" if not key.enabled or state.get("disabled") else "missing" if not configured else "wallet_mismatch" if family=="jina" and sum(item.account_id==key.account_id for item in executor.config.keys)!=1 else "unsupported_unit" if family=="you" and any(pool.unit!="usd" for pool in pools) else "cooldown" if state.get("cooldown",0)>time.time() else ("exhausted" if confirmed else "unconfirmed") if not available else "ready")
        balance=payload["balances"].get(key.account_id,{})
        if status in ("ready","exhausted","unconfirmed") and balance.get("observed_at"):
            if balance.get("stale"):
                status="stale_balance"
            elif not balance.get("budget_verified") and not uses_total_ceiling(balance):
                status="unverified_balance"
        row=stats.get(key.id,{})
        payload["keys"].append({"id":key.id,"account_id":key.account_id,"label":key.label,"pool_ids":list(key.pool_ids),"enabled":key.enabled,
            "configured":configured,"status":status,"calls":row.get("calls",0),"successes":row.get("successes",0),
            "consumed":str(row.get("consumed_units",0)/1_000_000),"unit":pools[0].unit})
    units_by_key={k.id:executor.pools[k.pool_ids[0]].unit for k in executor.config.keys}
    for row in ledger.calls(limit=100,start=start,end=end):
        if not start <= row["ts"] < end:
            continue
        payload["calls"].append({**row,"unit":units_by_key.get(row["key_id"],"unknown")})
    return payload


def create_admin_app(get_oracle, token: str):
    async def body_json(request,limit=16384):
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>limit:
                raise ValueError("request too large")
            chunks.append(chunk)
        import json
        return json.loads(b"".join(chunks))

    async def index(request):
        return HTMLResponse((STATIC/"index.html").read_text(encoding="utf-8").replace("__CSP_NONCE__",request.state.csp_nonce))

    async def api(request):
        month=request.query_params.get("month",datetime.now(timezone.utc).strftime("%Y-%m"))
        try:
            month_bounds(month)
        except (ValueError,OverflowError):
            return JSONResponse({"error":"invalid month; expected YYYY-MM"},status_code=400)
        return JSONResponse(overview(get_oracle(),month))

    async def trace_detail(request):
        executor=getattr(get_oracle().registry,'credentials',None)
        trace_id=request.path_params['trace_id']
        detail=executor.ledger.trace_detail(trace_id) if executor and re.fullmatch('[a-f0-9]{32}',trace_id) else None
        if detail is None:
            return JSONResponse({'error':'trace not found'},status_code=404)
        return JSONResponse(detail)

    async def sync_balances(request):
        executor=getattr(get_oracle().registry,"credentials",None)
        if executor is None:
            return JSONResponse({"error":"multi-account mode required"},status_code=409)
        try:
            body=await body_json(request,8192)
            if not isinstance(body,dict) or set(body)-{"account_ids"}:
                raise ValueError()
            ids=body.get("account_ids")
            if ids is not None and (not isinstance(ids,list) or any(not isinstance(item,str) for item in ids)):
                raise ValueError()
            results=await executor.balance_sync.run(ids)
        except (ValueError,TypeError):
            return JSONResponse({"error":"invalid account selection"},status_code=400)
        return JSONResponse({"results":results})

    async def manage(request):
        from .management import ConfigConflict,KeyCreate,KeyPatch,PoolConfirm
        from pydantic import ValidationError
        oracle=get_oracle()
        executor=getattr(oracle.registry,"credentials",None)
        if executor is None or not executor.manager.enabled:
            return JSONResponse({"error":"encrypted credential management is disabled"},status_code=409)
        if request.headers.get("x-oracle-action")!="manage" or request.headers.get("content-type","").split(';')[0]!="application/json":
            return JSONResponse({"error":"explicit JSON management request required"},status_code=403)
        try:
            body=await body_json(request)
            has_secret=isinstance(body,dict) and "secret" in body
            loopback=request.url.hostname in ("localhost","127.0.0.1","::1") and request.client and request.client.host in ("127.0.0.1","::1","localhost")
            if has_secret and request.url.scheme!="https" and not loopback:
                return JSONResponse({"error":"HTTPS or local loopback required for credentials"},status_code=403)
            if request.method=="POST":
                key_id=executor.manager.create(KeyCreate.model_validate(body))
            elif "pool_id" in request.path_params:
                executor.manager.confirm_pool(request.path_params["pool_id"],PoolConfirm.model_validate(body))
                key_id=None
            else:
                key_id=request.path_params["key_id"]
                executor.manager.patch(key_id,KeyPatch.model_validate(body))
            refresh=getattr(oracle.registry,"refresh_credentials",None)
            if refresh: refresh()
            return JSONResponse({"revision":executor.revision,"key_id":key_id})
        except ConfigConflict:
            return JSONResponse({"error":"configuration changed; reload before editing"},status_code=409)
        except (ValidationError,ValueError,TypeError):
            return JSONResponse({"error":"invalid change; check fields, duplicate keys and quota reconciliation requirements"},status_code=400)

    async def dismiss_alert(request):
        executor=getattr(get_oracle().registry,'credentials',None)
        if executor is None:
            return JSONResponse({'error':'multi-account mode required'},status_code=409)
        if request.headers.get('x-oracle-action')!='manage' or request.headers.get('content-type','').split(';')[0]!='application/json':
            return JSONResponse({'error':'explicit JSON management request required'},status_code=403)
        try:
            body=await body_json(request,1024)
            if not isinstance(body,dict) or set(body)!={'version'} or type(body['version']) is not int or body['version']<1:
                raise ValueError()
        except (ValueError,TypeError):
            return JSONResponse({'error':'invalid alert version'},status_code=400)
        try:
            executor.ledger.dismiss_alert(request.path_params['alert_id'],body['version'])
        except ValueError:
            return JSONResponse({'error':'alert changed; reload before dismissing'},status_code=409)
        return JSONResponse({'ok':True})

    app=Starlette(routes=[Route("/",index),Route("/api/overview",api),Route("/api/traces/{trace_id}",trace_detail),Route("/api/balances/sync",sync_balances,methods=["POST"]),
        Route("/api/keys",manage,methods=["POST"]),Route("/api/keys/{key_id}",manage,methods=["PATCH"]),Route("/api/pools/{pool_id}",manage,methods=["PATCH"]),Route('/api/alerts/{alert_id}/dismiss',dismiss_alert,methods=['POST']),Mount("/assets",StaticFiles(directory=STATIC))])
    failures=defaultdict(deque)

    class AdminBoundary(BaseHTTPMiddleware):
        async def dispatch(self,request,call_next):
            request.state.csp_nonce=secrets.token_urlsafe(24)
            if not token:
                return JSONResponse({"error":"admin disabled"},status_code=404)
            if request.url.path.startswith("/admin/api/") or request.url.path.startswith("/api/"):
                origin=request.headers.get("origin")
                if origin and origin!=f"{request.url.scheme}://{request.url.netloc}":
                    return JSONResponse({"error":"cross origin request rejected"},status_code=403)
                address=request.client.host if request.client else "unknown"
                if address not in failures and len(failures)>=1024:
                    failures.pop(next(iter(failures)))
                attempts=failures[address]
                now=time.monotonic()
                while attempts and attempts[0]<now-60: attempts.popleft()
                if len(attempts)>=10:
                    return JSONResponse({"error":"too many authentication attempts"},status_code=429,headers={"Retry-After":"60"})
                supplied=request.headers.get("authorization","")
                supplied=supplied[7:] if supplied.lower().startswith("bearer ") else ""
                if not secrets.compare_digest(supplied.encode("utf-8"),token.encode("utf-8")):
                    attempts.append(now)
                    return JSONResponse({"error":"unauthorized"},status_code=401)
                attempts.clear()
            response=await call_next(request)
            response.headers["Cache-Control"]="no-store"
            response.headers["X-Content-Type-Options"]="nosniff"
            response.headers["Referrer-Policy"]="no-referrer"
            response.headers["Content-Security-Policy"]=f"default-src 'self'; script-src 'self'; style-src 'self' 'nonce-{request.state.csp_nonce}'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            return response
    app.add_middleware(AdminBoundary)
    return app
