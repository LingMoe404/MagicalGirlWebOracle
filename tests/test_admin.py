"""Quota dashboard permissions, accounting and packaging surface tests."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from magicalgirl_web_oracle.admin.app import create_admin_app, month_bounds
from magicalgirl_web_oracle.mcp_server.server import _with_auth
from magicalgirl_web_oracle.providers.key_pool import CredentialExecutor
from magicalgirl_web_oracle.quota.accounts import AccountConfig
from magicalgirl_web_oracle.quota.ledger import AccountLedger


@pytest.fixture
def admin_fixture(tmp_path,settings):
    config=AccountConfig.model_validate({
        "accounts":[{"id":"a","label":"Google-A","provider":"tavily"}],
        "pools":[{"id":"p","account_id":"a","unit":"credits","limit":1000,"initial_used":12,"free_allowance_confirmed":True,"window":{"kind":"monthly","reset_day":1}}],
        "keys":[{"id":"k1","account_id":"a","label":"Primary","pool_ids":["p"],"secret_env":"FAKE_KEY_ONE"},
                {"id":"k2","account_id":"a","label":"Secondary","pool_ids":["p"],"secret_env":"FAKE_KEY_TWO"}],
    })
    ledger=AccountLedger(str(tmp_path/"ledger.sqlite3"))
    executor=CredentialExecutor(settings,config,ledger,environ={"FAKE_KEY_ONE":"never-expose-one","FAKE_KEY_TWO":"never-expose-two"})
    oracle=SimpleNamespace(registry=SimpleNamespace(credentials=executor))
    mounted=Starlette(routes=[Mount("/admin",create_admin_app(lambda:oracle,"test-admin-token"))])
    # Admin access must work independently of the external MCP middleware.
    app=_with_auth(mounted,"test-mcp-token")
    with TestClient(app) as client:
        yield client,executor
    ledger.close()


AUTH={"Authorization":"Bearer test-admin-token"}


def test_admin_disabled_by_default():
    app=create_admin_app(lambda:None,"")
    with TestClient(app) as client:
        assert client.get("/").status_code==404
        assert client.get("/api/overview",headers=AUTH).status_code==404


def test_independent_admin_auth_and_cross_origin_rejection(admin_fixture):
    client,_=admin_fixture
    assert client.get("/admin/").status_code==200
    assert client.get("/admin/api/overview").status_code==401
    assert client.get("/admin/api/overview",headers={"Authorization":"Bearer test-mcp-token"}).status_code==401
    assert client.get("/admin/api/overview",headers={**AUTH,"Origin":"https://attacker.invalid"}).status_code==403
    assert client.get("/admin/api/overview",headers=AUTH).status_code==200


def test_quota_pool_is_shared_and_output_has_no_secrets(admin_fixture):
    client,executor=admin_fixture
    key=executor.config.keys[0]
    call=executor.ledger.reserve(key,executor.pools,"tavily","search",2)
    executor.ledger.dispatch(call);executor.ledger.settle(call,2)
    response=client.get("/admin/api/overview",headers=AUTH)
    data=response.json()
    assert len(data["accounts"])==1 and len(data["pools"])==1 and len(data["keys"])==2
    assert data["pools"][0]["remaining"]=="986"
    assert data["keys"][0]["calls"]==1 and data["keys"][1]["calls"]==0
    assert data["keys"][0]["consumed"]=="2.0"
    assert data["pools"][0]["source"]=="estimated"
    for forbidden in ("never-expose", "FAKE_KEY", "fingerprint", "secret_env"):
        assert forbidden not in response.text
    assert response.headers["cache-control"]=="no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"]=="nosniff"


def test_month_changes_statistics_but_never_historical_balance(admin_fixture):
    client,executor=admin_fixture
    call=executor.ledger.reserve(executor.config.keys[0],executor.pools,"tavily","search",1)
    executor.ledger.dispatch(call);executor.ledger.settle(call,1)
    executor.ledger.db.execute("UPDATE calls SET ts=? WHERE id=?",(datetime(2020,1,15,tzinfo=timezone.utc).timestamp(),call))
    old=client.get("/admin/api/overview?month=2020-01",headers=AUTH).json()
    current=client.get("/admin/api/overview",headers=AUTH).json()
    assert old["keys"][0]["calls"]==1 and current["keys"][0]["calls"]==0
    assert old["pools"]==current["pools"]
    assert len(old["calls"])==1 and len(current["calls"])==0


def test_invalid_month_and_auth_throttling(admin_fixture):
    client,_=admin_fixture
    for month in ("invalid","2026-13","2026-00","0000-01","9999-12"):
        assert client.get(f"/admin/api/overview?month={month}",headers=AUTH).status_code==400
    for _ in range(10):
        assert client.get("/admin/api/overview").status_code==401
    response=client.get("/admin/api/overview",headers=AUTH)
    assert response.status_code==429 and response.headers["retry-after"]=="60"


def test_assets_are_served_with_no_third_party_dependencies(admin_fixture):
    client,_=admin_fixture
    response=client.get("/admin/assets/app.js")
    assert response.status_code==200
    assert "localStorage" not in response.text and "sessionStorage" not in response.text
    assert 'credentials:"omit"' in response.text
    assert client.get("/admin/assets/index.css").status_code==200
    assert "http" not in client.get("/admin/").text


def test_month_bounds_are_half_open_and_cross_year():
    start,end=month_bounds("2026-12")
    assert datetime.fromtimestamp(start,timezone.utc)==datetime(2026,12,1,tzinfo=timezone.utc)
    assert datetime.fromtimestamp(end,timezone.utc)==datetime(2027,1,1,tzinfo=timezone.utc)


@pytest.mark.parametrize("transport",["streamable-http","sse"])
def test_actual_http_stack_builds_with_both_tokens(transport,monkeypatch,settings):
    from magicalgirl_web_oracle.mcp_server import server
    settings.admin_auth_token="test-admin-token"
    settings.mcp_auth_token="test-mcp-token"
    monkeypatch.setattr(server,"_settings",settings)
    app=server._build_http_app(transport)
    with TestClient(app) as client:
        assert client.get("/admin/").status_code==200
        assert client.get("/admin/api/overview",headers={"Authorization":"Bearer test-mcp-token"}).status_code==401
        assert client.post("/mcp").status_code==401


def test_same_admin_and_mcp_token_rejected(monkeypatch,settings):
    from magicalgirl_web_oracle.mcp_server import server
    settings.admin_auth_token=settings.mcp_auth_token="same-token"
    monkeypatch.setattr(server,"_settings",settings)
    with pytest.raises(ValueError,match="different"):
        server._build_http_app("streamable-http")


def test_fluent_styles_receive_unique_csp_nonce(admin_fixture):
    import re
    client,_=admin_fixture
    first=client.get("/admin/")
    second=client.get("/admin/")
    nonce=re.search(r'name="csp-nonce" content="([^"]+)"',first.text).group(1)
    other=re.search(r'name="csp-nonce" content="([^"]+)"',second.text).group(1)
    assert nonce!=other and nonce!="__CSP_NONCE__"
    assert f"'nonce-{nonce}'" in first.headers["content-security-policy"]
    assert "unsafe-inline" not in first.headers["content-security-policy"]


def test_balance_sync_requires_admin_auth_and_does_not_accept_cross_origin(admin_fixture):
    client,_=admin_fixture
    assert client.post("/admin/api/balances/sync",json={}).status_code==401
    assert client.post("/admin/api/balances/sync",json={},headers={**AUTH,"Origin":"https://attacker.invalid"}).status_code==403
    assert client.post("/admin/api/balances/sync",json={"account_ids":["missing"]},headers=AUTH).status_code==400
    assert client.post("/admin/api/balances/sync",json={"account_ids":"a"},headers=AUTH).status_code==400


def test_balance_sync_endpoint_and_history_are_account_scoped(admin_fixture):
    from magicalgirl_web_oracle.quota.balances import BalanceSync
    client,executor=admin_fixture
    class Provider:
        def __init__(self,s): pass
        async def fetch_balance(self): return {"plan":"Researcher","used":25,"limit":1000}
        async def aclose(self): pass
    executor.balance_sync=BalanceSync(executor,factories={"tavily":(Provider,"tavily_api_key")})
    response=client.post("/admin/api/balances/sync",json={"account_ids":["a"]},headers=AUTH)
    assert response.status_code==200 and response.json()["results"][0]["status"]=="ok"
    data=client.get("/admin/api/overview",headers=AUTH).json()
    assert data["pools"][0]["official_remaining"]=="975"
    assert data["pools"][0]["remaining"]=="975"
    assert len(data["balance_history"])==1
    assert len(data["balances"])==1
    assert "secret" not in str(data)


def test_alert_dismissal_is_authenticated_persistent_and_versioned(admin_fixture):
    client,executor=admin_fixture
    executor.ledger.penalize(executor.config.keys[0],'auth')
    data=client.get('/admin/api/overview',headers=AUTH).json()
    assert len(data['alerts'])==1 and data['alerts'][0]['kind']=='invalid_key'
    alert=data['alerts'][0]
    path=f"/admin/api/alerts/{alert['id']}/dismiss"
    body={'version':alert['version']}
    assert client.post(path,json=body).status_code==401
    assert client.post(path,json=body,headers=AUTH).status_code==403
    explicit={**AUTH,'X-Oracle-Action':'manage'}
    assert client.post(path,json=body,headers={**explicit,'Origin':'https://attacker.invalid'}).status_code==403
    assert client.post(path,json={'version':0},headers=explicit).status_code==400
    assert client.post(path,json=body,headers=explicit).status_code==200
    updated=client.get('/admin/api/overview',headers=AUTH).json()
    assert updated['alerts'][0]['dismissed_at'] is not None
    assert len(updated['alert_history'])==2
    assert 'never-expose' not in str(updated['alert_history'])


def test_trace_detail_requires_admin_and_history_is_month_scoped(admin_fixture):
    client,executor=admin_fixture
    ledger=executor.ledger
    trace=ledger.start_trace('web_search')
    call=ledger.reserve(executor.config.keys[0],executor.pools,'tavily','search',1)
    ledger.append_trace(trace,'reserved',dict(provider='tavily',call_id=call,key_id='k1'))
    ledger.dispatch(call);ledger.settle(call,1)
    ledger.finish_trace(trace,'success',12)
    path=f'/admin/api/traces/{trace}'
    assert client.get(path).status_code==401
    assert client.get(path,headers={'Authorization':'Bearer test-mcp-token'}).status_code==401
    assert client.get(path,headers={**AUTH,'Origin':'https://attacker.invalid'}).status_code==403
    response=client.get(path,headers=AUTH)
    assert response.status_code==200
    assert response.json()['calls'][0]['allocations'][0]['unit']=='credits'
    for forbidden in ('never-expose','FAKE_KEY','fingerprint','secret_env'):
        assert forbidden not in response.text
    assert response.headers['cache-control']=='no-store'
    assert len(client.get('/admin/api/overview',headers=AUTH).json()['traces'])==1
    assert client.get('/admin/api/traces/not-an-id',headers=AUTH).status_code==404
    assert client.get('/admin/api/traces/'+'a'*32,headers=AUTH).status_code==404
    ledger.db.execute('UPDATE request_traces SET ts=? WHERE id=?',(datetime(2020,1,15,tzinfo=timezone.utc).timestamp(),trace))
    assert client.get('/admin/api/overview',headers=AUTH).json()['traces']==[]
    assert len(client.get('/admin/api/overview?month=2020-01',headers=AUTH).json()['traces'])==1
