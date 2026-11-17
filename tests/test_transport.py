"""Tests for MCP transport config, HTTP app construction, and auth middleware."""

from __future__ import annotations

import json

import pytest

from magicalgirl_web_oracle.config.settings import Settings

from conftest import DummySettings


# ---------------------------------------------------------------------------
# Transport settings
# ---------------------------------------------------------------------------

def test_transport_defaults_to_stdio(monkeypatch):
    for var in ("MCP_TRANSPORT", "MCP_HOST", "MCP_PORT", "MCP_PATH",
                "MCP_AUTH_TOKEN", "MCP_BIND_IP"):
        monkeypatch.delenv(var, raising=False)
    s = DummySettings()
    assert s.mcp_transport == "stdio"
    assert s.mcp_host == "127.0.0.1"
    assert s.mcp_port == 28288
    assert s.mcp_path == "/mcp"
    assert s.mcp_auth_token == ""
    assert s.mcp_bind_ip == "127.0.0.1"  # loopback unless explicitly exposed


def test_bind_ip_can_pin_to_wireguard(monkeypatch):
    monkeypatch.setenv("MCP_BIND_IP", "10.8.0.1")
    s = DummySettings()
    assert s.mcp_bind_ip == "10.8.0.1"
    assert s.masked_summary()["mcp_bind_ip"] == "10.8.0.1"


def test_transport_env_override(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("MCP_PORT", "9999")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "secret-token")
    s = DummySettings()
    assert s.mcp_transport == "streamable-http"
    assert s.mcp_port == 9999
    assert s.mcp_auth_token == "secret-token"


def test_masked_summary_shows_auth_state_not_token():
    s = DummySettings()
    s.mcp_auth_token = "super-secret-token-value"
    summary = s.masked_summary()
    assert summary["mcp_auth"] == "enabled"
    assert "super-secret-token-value" not in json.dumps(summary)


def test_masked_summary_auth_disabled_when_empty():
    s = DummySettings()
    s.mcp_auth_token = ""
    assert s.masked_summary()["mcp_auth"] == "disabled"


# ---------------------------------------------------------------------------
# HTTP app construction + auth middleware
# ---------------------------------------------------------------------------

def test_http_app_builds_and_has_mcp_route():
    """streamable_http_app must build without network access."""
    from magicalgirl_web_oracle.mcp_server.server import mcp
    app = mcp.streamable_http_app()
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/mcp" in paths


@pytest.mark.asyncio
async def test_auth_middleware_rejects_missing_token():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from magicalgirl_web_oracle.mcp_server.server import _with_auth

    async def endpoint(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/mcp", endpoint, methods=["POST"])])
    app = _with_auth(app, "correct-token")

    client = TestClient(app)
    assert client.post("/mcp").status_code == 401
    assert client.post("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.post("/mcp", headers={"Authorization": "Bearer correct-token"})
    assert ok.status_code == 200 and ok.text == "ok"


@pytest.mark.asyncio
async def test_auth_middleware_allows_healthz_unauthenticated():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from magicalgirl_web_oracle.mcp_server.server import _with_auth

    async def endpoint(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/healthz", endpoint)])
    app = _with_auth(app, "correct-token")
    client = TestClient(app)
    # Health probes must work without a token (container healthchecks).
    assert client.get("/healthz").status_code == 200


def test_utf8_charset_added_to_sse_responses():
    """Without a charset, a cp936 client decodes UTF-8 as GBK => mojibake."""
    from starlette.applications import Starlette
    from starlette.responses import StreamingResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from magicalgirl_web_oracle.mcp_server.server import _with_utf8_charset

    async def sse(request):
        async def gen():
            yield "data: 中文 النطاق\n\n".encode("utf-8")
        return StreamingResponse(gen(), media_type="text/event-stream")

    app = Starlette(routes=[Route("/mcp", sse, methods=["POST"])])
    app = _with_utf8_charset(app)
    with TestClient(app) as client:
        r = client.post("/mcp")
    assert "charset=utf-8" in r.headers["content-type"].lower()
    # And the bytes still decode cleanly rather than being re-encoded.
    assert "中文 النطاق" in r.content.decode("utf-8")


def test_utf8_charset_leaves_existing_charset_alone():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from magicalgirl_web_oracle.mcp_server.server import _with_utf8_charset

    async def endpoint(request):
        return PlainTextResponse("ok", media_type="text/plain; charset=iso-8859-1")

    app = _with_utf8_charset(Starlette(routes=[Route("/x", endpoint)]))
    with TestClient(app) as client:
        r = client.get("/x")
    assert "iso-8859-1" in r.headers["content-type"].lower()
    assert "charset=utf-8" not in r.headers["content-type"].lower()


def test_utf8_charset_skips_non_text_responses():
    from starlette.applications import Starlette
    from starlette.responses import Response
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from magicalgirl_web_oracle.mcp_server.server import _with_utf8_charset

    async def endpoint(request):
        return Response(b"\x00\x01", media_type="application/octet-stream")

    app = _with_utf8_charset(Starlette(routes=[Route("/bin", endpoint)]))
    with TestClient(app) as client:
        r = client.get("/bin")
    assert r.headers["content-type"] == "application/octet-stream"


# ---------------------------------------------------------------------------
# Concurrency: usage DB writes are safe under parallel access
# ---------------------------------------------------------------------------

def test_usage_db_concurrent_writes(usage):
    import threading

    def worker(n):
        for i in range(20):
            usage.record(f"prov{n % 3}", success=True, credits=1.0, query=f"q{n}-{i}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total = usage._conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
    assert total == 120  # no lost writes, no lock errors


def test_usage_db_wal_mode_enabled(usage):
    mode = usage._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
