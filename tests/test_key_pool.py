"""Multi-key execution and durable reservation tests, with no external calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import multiprocessing

import pytest

from magicalgirl_web_oracle.providers.base import ProviderError
from magicalgirl_web_oracle.providers.key_pool import CredentialExecutor, load_accounts
from magicalgirl_web_oracle.providers.registry import Registry
from magicalgirl_web_oracle.quota.accounts import AccountConfig
from magicalgirl_web_oracle.quota.ledger import AccountLedger, BudgetUnavailable, units
from magicalgirl_web_oracle.router.oracle import Oracle


def account_config(*, shared=False, limit=5, initial=0, confirmed=True, family="tavily", unit="credits"):
    accounts = [{"id":"a","provider":family,"label":"Google-A"}]
    pools = [{"id":"p-a","account_id":"a","unit":unit,"limit":limit,"initial_used":initial,
              "free_allowance_confirmed":confirmed,"window":{"kind":"monthly","reset_day":1}}]
    if not shared:
        accounts.append({"id":"b","provider":family,"label":"Google-B"})
        pools.append({**pools[0],"id":"p-b","account_id":"b"})
    keys = [dict(id="k-a",account_id="a",label="A",secret_env="KEY_A",pool_ids=["p-a"],priority=1),
            dict(id="k-b",account_id="a" if shared else "b",label="B",secret_env="KEY_B",pool_ids=["p-a" if shared else "p-b"],priority=2)]
    return AccountConfig.model_validate(dict(accounts=accounts,pools=pools,keys=keys))


def runtime(tmp_path, settings, **kwargs):
    cfg = account_config(**kwargs)
    ledger = AccountLedger(str(tmp_path/"accounts.sqlite3"))
    executor = CredentialExecutor(settings,cfg,ledger,environ={"KEY_A":"fake-a","KEY_B":"fake-b"})
    return cfg,ledger,executor


def test_last_credit_is_atomic_across_connections(tmp_path,settings):
    cfg,ledger,executor = runtime(tmp_path,settings,shared=True,limit=1)
    other = AccountLedger(str(ledger.path))
    def reserve(db):
        try:
            return db.reserve(cfg.keys[0],executor.pools,"tavily","search",1)
        except BudgetUnavailable:
            return None
    with ThreadPoolExecutor(max_workers=2) as threads:
        results = list(threads.map(reserve,[ledger,other]))
    assert sum(r is not None for r in results) == 1
    assert ledger.remaining(cfg.pools[0]) == 0
    other.close(); ledger.close()


def process_reserve(path, source, queue):
    cfg = AccountConfig.model_validate(source)
    ledger = AccountLedger(path)
    try:
        ledger.reserve(cfg.keys[0],{p.id:p for p in cfg.pools},"tavily","search",1)
        queue.put(True)
    except BudgetUnavailable:
        queue.put(False)
    finally:
        ledger.close()


def test_last_credit_is_atomic_across_processes(tmp_path,settings):
    cfg,ledger,_ = runtime(tmp_path,settings,shared=True,limit=1)
    source=cfg.model_dump(mode="json")
    for row,key in zip(source["keys"],cfg.keys):
        row["secret_env"]=key.secret_env
    context=multiprocessing.get_context("spawn")
    queue=context.Queue()
    processes=[context.Process(target=process_reserve,args=(str(ledger.path),source,queue)) for _ in range(2)]
    try:
        for process in processes: process.start()
        for process in processes:
            process.join(timeout=15)
            assert process.exitcode==0
        assert sorted([queue.get(timeout=2),queue.get(timeout=2)])==[False,True]
        assert ledger.remaining(cfg.pools[0])==0
    finally:
        for process in processes:
            if process.is_alive(): process.terminate(); process.join(timeout=5)
        queue.close()
        ledger.close()


def test_budget_reservation_survives_restart_and_cannot_be_released_after_dispatch(tmp_path,settings):
    cfg,ledger,executor = runtime(tmp_path,settings,shared=True,limit=2)
    call = ledger.reserve(cfg.keys[0],executor.pools,"tavily","search",2)
    ledger.dispatch(call)
    ledger.close()
    reopened = AccountLedger(str(tmp_path/"accounts.sqlite3"))
    assert reopened.remaining(cfg.pools[0]) == 0
    with pytest.raises(ValueError,match="undispatched"):
        reopened.release(call)
    reopened.uncertain(call,"timeout")
    assert reopened.remaining(cfg.pools[0]) == 0
    reopened.settle(call,1)
    reopened.settle(call,1)  # idempotent
    assert reopened.remaining(cfg.pools[0]) == 1
    with pytest.raises(ValueError,match="conflicting"):
        reopened.settle(call,0)
    reopened.close()


def test_no_implicit_baseline_and_no_pool_reset_on_reregister(tmp_path,settings):
    cfg,ledger,executor = runtime(tmp_path,settings,initial=None)
    assert ledger.remaining(cfg.pools[0]) == 0
    with pytest.raises(BudgetUnavailable):
        ledger.reserve(cfg.keys[0],executor.pools,"tavily","search",1)
    ledger.close()
    cfg,ledger,executor = runtime(tmp_path/"other",settings,shared=True,initial=3)
    assert ledger.remaining(cfg.pools[0]) == 2
    ledger.register(cfg,executor._secrets)
    assert ledger.remaining(cfg.pools[0]) == 2
    changed = cfg.model_copy(update={"pools":(cfg.pools[0].model_copy(update={"initial_used":Decimal(0)}),)})
    with pytest.raises(ValueError,match="configuration changed"):
        ledger.register(changed,executor._secrets)
    assert ledger.remaining(cfg.pools[0]) == 2
    ledger.close()


def test_duplicate_material_rejected_without_partial_registration(tmp_path,settings):
    cfg = account_config()
    ledger = AccountLedger(str(tmp_path/"accounts.sqlite3"))
    with pytest.raises(ValueError,match="duplicate credential"):
        CredentialExecutor(settings,cfg,ledger,environ={"KEY_A":"same-fake-key","KEY_B":"same-fake-key"})
    assert ledger.db.execute("SELECT COUNT(*) FROM pools").fetchone()[0] == 0
    assert ledger.db.execute("SELECT COUNT(*) FROM credentials").fetchone()[0] == 0
    ledger.close()


def test_waterfall_is_atomic_and_settles_in_configured_order(tmp_path,settings):
    cfg = account_config(shared=True,limit=1)
    bonus = cfg.pools[0].model_copy(update={"id":"bonus"})
    key = cfg.keys[0].model_copy(update={"pool_ids":("p-a","bonus")})
    cfg = cfg.model_copy(update={"pools":(*cfg.pools,bonus),"keys":(key,)})
    ledger = AccountLedger(str(tmp_path/"accounts.sqlite3"))
    executor = CredentialExecutor(settings,cfg,ledger,environ={"KEY_A":"fake-a"})
    with pytest.raises(BudgetUnavailable):
        ledger.reserve(key,executor.pools,"tavily","search",3)
    assert ledger.calls() == []
    call = ledger.reserve(key,executor.pools,"tavily","search",2)
    ledger.dispatch(call); ledger.settle(call,"1.5")
    assert ledger.remaining(cfg.pools[0]) == 0
    assert ledger.remaining(bonus) == Decimal("0.5")
    ledger.close()


def test_uncertain_reservations_carry_over_month_boundary(tmp_path,settings):
    cfg,ledger,executor = runtime(tmp_path,settings,shared=True,limit=5)
    call = ledger.reserve(cfg.keys[0],executor.pools,"tavily","search",2)
    ledger.dispatch(call); ledger.uncertain(call,"timeout")
    future = datetime.now(timezone.utc)+timedelta(days=40)
    assert ledger.remaining(cfg.pools[0],future.timestamp()) == 3
    ledger.settle(call,2)
    assert ledger.remaining(cfg.pools[0],future.timestamp()) == 5
    ledger.close()


def test_overrun_retains_actual_cost_and_blocks_pool(tmp_path,settings):
    cfg,ledger,executor = runtime(tmp_path,settings,shared=True,limit=5)
    call=ledger.reserve(cfg.keys[0],executor.pools,"tavily","search",1)
    ledger.dispatch(call); ledger.settle(call,2)
    assert ledger.remaining(cfg.pools[0]) == 0
    assert ledger.calls()[0]["charged_units"] == 2_000_000
    assert ledger.calls()[0]["error"] == "cost_bound_exceeded"
    ledger.close()


@pytest.mark.parametrize("value",[-1,"NaN","Infinity","0.0000001"])
def test_invalid_reservation_amounts(value):
    with pytest.raises(ValueError):
        units(value)


@pytest.mark.asyncio
async def test_auth_failure_switches_to_second_key_without_mutating_settings(tmp_path,settings):
    cfg,ledger,executor = runtime(tmp_path,settings)
    calls=[]
    class Fake:
        def __init__(self,s): self.settings=s
        async def search(self,query,**kwargs):
            calls.append(self.settings.tavily_api_key)
            if self.settings.tavily_api_key == "fake-a":
                raise ProviderError("echo fake-a secret",401,"auth")
            return ["success"]
        async def aclose(self): pass
    original=settings.tavily_api_key
    assert await executor.execute("tavily",Fake,"search","query",mode="high") == ["success"]
    assert calls == ["fake-a","fake-b"]
    assert settings.tavily_api_key == original
    assert ledger.remaining(cfg.pools[0]) == 3  # failure has uncertain consumption
    assert ledger.remaining(cfg.pools[1]) == 3  # advanced=2, never 1
    assert "fake-a" not in str(ledger.calls())
    assert ledger.db.execute("SELECT disabled FROM credentials WHERE id='k-a'").fetchone()[0] == 1
    await executor.execute("tavily",Fake,"search","query",mode="fast")
    assert calls == ["fake-a","fake-b","fake-b"]
    ledger.close()


@pytest.mark.asyncio
async def test_shared_exhaustion_does_not_retry_other_key_or_create_second_crawl(tmp_path,settings):
    cfg,ledger,executor = runtime(tmp_path,settings,shared=True,family="firecrawl",limit=1)
    calls=[]
    class Fake:
        def __init__(self,s): pass
        async def crawl_site(self,*args,**kwargs):
            calls.append("create")
            raise ProviderError("timeout",kind="timeout")
        async def aclose(self): pass
    with pytest.raises(ProviderError,match="retry suppressed"):
        await executor.execute("firecrawl",Fake,"crawl_site","url",max_pages=1)
    assert len(calls)==1 and ledger.remaining(cfg.pools[0])==0
    ledger.close()


@pytest.mark.asyncio
async def test_cancelled_call_retains_budget(tmp_path,settings):
    cfg,ledger,executor = runtime(tmp_path,settings,shared=True)
    class Fake:
        def __init__(self,s): pass
        async def search(self,*args,**kwargs): raise asyncio.CancelledError()
        async def aclose(self): pass
    with pytest.raises(asyncio.CancelledError):
        await executor.execute("tavily",Fake,"search","q")
    assert ledger.remaining(cfg.pools[0])==4
    assert ledger.calls()[0]["state"]=="uncertain"
    ledger.close()


@pytest.mark.asyncio
async def test_registry_routes_real_adapter_through_new_ledger(tmp_path,settings,monkeypatch):
    import yaml
    import httpx
    from magicalgirl_web_oracle.providers.tavily import TavilyProvider
    cfg=account_config(shared=True,limit=1)
    # Public serialization intentionally drops credential refs; write the
    # secret-free source representation explicitly.
    source=cfg.model_dump(mode="json")
    for row,key in zip(source["keys"],cfg.keys): row["secret_env"]=key.secret_env
    path=tmp_path/"accounts.yaml"; path.write_text(yaml.safe_dump(source),encoding="utf-8")
    settings.accounts_file=str(path)
    monkeypatch.setenv("KEY_A","fake-a"); monkeypatch.setenv("KEY_B","fake-b")
    called=[]
    async def client(self):
        if self._client is None:
            def handle(req):
                called.append(req.headers["authorization"])
                return httpx.Response(200,json={"results":[{"url":"https://example.com","title":"Test"}]})
            self._client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        return self._client
    monkeypatch.setattr(TavilyProvider,"client",client)
    oracle=Oracle(settings=settings)
    # Legacy bookkeeping must not incorrectly block the new account budget.
    oracle.usage.record("tavily",success=True,credits=100000)
    first=await oracle.web_search("q",provider_override="tavily")
    second=await oracle.web_search("q",provider_override="tavily")
    assert len(first.results)==1 and second.results==[]
    assert called==["Bearer fake-a"]
    assert len(oracle.registry.credentials.ledger.calls())==1
    assert oracle.status().providers[3].quota.remaining==0
    await oracle.aclose()


def test_bad_configuration_error_does_not_echo_input(tmp_path):
    path=tmp_path/"bad.yaml"; path.write_text("secret: fake-secret\n",encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_accounts(str(path))
    assert "fake-secret" not in str(exc.value)


def test_unknown_cost_unit_cannot_dispatch(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,family="jina",unit="tokens")
    with pytest.raises(ProviderError,match="not verified"):
        executor.estimate("jina_reranker","rerank",{})
    assert ledger.calls()==[]
    ledger.close()


@pytest.mark.asyncio
async def test_zero_reported_exa_cost_is_not_replaced_with_one_credit(tmp_path,settings):
    cfg,ledger,executor=runtime(tmp_path,settings,family="exa",unit="usd",limit=1)
    class Fake:
        last_cost_reported=True
        last_cost_usd=0
        def __init__(self,s): pass
        async def search(self,*args,**kwargs): return []
        async def aclose(self): pass
    await executor.execute("exa",Fake,"search","q",max_results=10)
    assert ledger.remaining(cfg.pools[0])==1
    assert ledger.calls()[0]["charged_units"]==0
    ledger.close()


@pytest.mark.asyncio
async def test_firecrawl_fetch_accounts_for_its_reported_cost(tmp_path,settings):
    from magicalgirl_web_oracle.models.schemas import FetchedDocument
    cfg,ledger,executor=runtime(tmp_path,settings,family="firecrawl",limit=5)
    class Fake:
        def __init__(self,s): pass
        async def fetch(self,*args,**kwargs):
            return FetchedDocument(url="https://example.com",content="ok",provider="firecrawl",metadata={"credits_used":1})
        async def aclose(self): pass
    await executor.execute("firecrawl",Fake,"fetch","https://example.com")
    assert ledger.remaining(cfg.pools[0])==4
    ledger.close()


def test_example_configuration_is_valid_and_does_not_grant_budget():
    from pathlib import Path
    cfg=load_accounts(str(Path(__file__).parents[1]/"config/accounts.example.yaml"))
    assert len(cfg.accounts)==2 and len(cfg.keys)==2
    assert all(not pool.free_allowance_confirmed and pool.initial_used is None for pool in cfg.pools)
