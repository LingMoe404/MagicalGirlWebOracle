"""Phase 1 core tests: config, models, dedup/canonicalization, quota, health, langdetect."""

from __future__ import annotations

import time

import pytest

from magicalgirl_web_oracle.config.settings import Settings, get_settings
from magicalgirl_web_oracle.dedup.canonical import (agreement_score, canonicalize_url,
                                                   merge_results)
from magicalgirl_web_oracle.health.tracker import HealthTracker
from magicalgirl_web_oracle.langdetect.detector import china_relevance, detect_language
from magicalgirl_web_oracle.models.schemas import (FetchedDocument, QuotaType,
                                                   ResearchResponse, SearchResponse,
                                                   SearchResult)
from magicalgirl_web_oracle.quota.usage_db import UsageDB

from conftest import make_result


# ---------------------------------------------------------------------------
# Settings / config files
# ---------------------------------------------------------------------------

def test_settings_loads_and_never_raises_on_missing_env():
    s = get_settings()
    assert s.allow_paid_overage is False  # HARD RULE default
    assert s.default_mode in ("fast", "auto", "high", "max")


def test_providers_yaml_has_required_providers():
    s = Settings()
    providers = s.providers_cfg["providers"]
    for name in ("exa", "tavily", "firecrawl", "searxng", "crawl4ai", "you_mcp",
                 "jina_reader", "jina_reranker"):
        assert name in providers, f"missing {name} in providers.yaml"


def test_quota_types_correct():
    s = Settings()
    p = s.providers_cfg["providers"]
    assert p["searxng"]["quota"]["type"] == "unlimited"
    assert p["crawl4ai"]["quota"]["type"] == "unlimited"
    assert p["you_mcp"]["quota"]["type"] == "daily"
    assert p["exa"]["quota"]["type"] == "monthly"
    assert p["tavily"]["quota"]["type"] == "monthly"
    assert p["firecrawl"]["quota"]["type"] == "monthly"
    assert p["jina_reader"]["quota"]["type"] == "one_time"
    assert p["jina_reranker"]["quota"]["type"] == "one_time"


def test_router_policy_modes():
    s = Settings()
    modes = s.router_policy["mode_definitions"]
    assert set(modes) == {"fast", "auto", "high", "max"}
    assert modes["fast"]["rerank"] is False
    assert modes["max"]["rerank"] is True
    assert s.router_policy["credits_guard"]["allow_paid_overage"] is False


def test_masked_summary_hides_keys():
    s = Settings()
    s.exa_api_key = "sk-super-secret-key-123456"
    masked = s.masked_summary()
    assert "sk-super-secret-key-123456" not in str(masked)


# ---------------------------------------------------------------------------
# URL canonicalization / dedup
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,clean", [
    ("https://www.Example.com/a/", "https://example.com/a"),
    ("https://example.com/b?utm_source=x&utm_medium=y&id=1",
     "https://example.com/b?id=1"),
    ("https://example.com/c#section", "https://example.com/c"),
    ("http://example.com:80/d", "http://example.com/d"),
    ("https://example.com//a//b", "https://example.com/a/b"),
    ("https://example.com/e?fbclid=ABC123", "https://example.com/e"),
    ("https://example.com/f?spm=1.2.3.4", "https://example.com/f"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42",
     "https://youtube.com/watch?v=dQw4w9WgXcQ"),
    ("https://www.bilibili.com/video/BV1xx411c7mD/?p=1&spm_id_from=333",
     "https://bilibili.com/video/BV1xx411c7mD?p=1"),
    ("https://example.com/g?gclid=xyz&q=keep", "https://example.com/g?q=keep"),
])
def test_canonicalize_url(raw, clean):
    assert canonicalize_url(raw) == clean


def test_merge_results_dedups_and_merges_found_by():
    a = make_result("https://example.com/x?utm_source=exa", "exa", "Title X",
                    content="short")
    b = make_result("https://www.example.com/x/", "tavily", "Title X",
                    content="longer content than a")
    merged = merge_results([a, b])
    assert len(merged) == 1
    m = merged[0]
    assert set(m.found_by) == {"exa", "tavily"}
    assert m.content == "longer content than a"


def test_agreement_score():
    r = make_result("https://example.com/x", "exa")
    r.found_by = ["exa", "tavily", "searxng", "you_mcp"]
    scores = agreement_score([r])
    assert scores["https://example.com/x"] == 1.0  # capped at 3 independent sources


# ---------------------------------------------------------------------------
# Quota / usage DB
# ---------------------------------------------------------------------------

def test_usage_db_records_and_limits(usage: UsageDB):
    for _ in range(3):
        usage.record("exa", success=True, credits=1, mode="auto", query="test q")
    remaining = usage.remaining("exa", QuotaType.MONTHLY, 1000)
    assert remaining == 997
    # Raw query text NOT stored by default.
    rows = usage._conn.execute("SELECT query_text, query_hash FROM usage").fetchall()
    assert all(r[0] == "" for r in rows)
    assert all(r[1] for r in rows)


def test_usage_db_unlimited_has_no_limit(usage: UsageDB):
    usage.record("searxng", success=True)
    assert usage.remaining("searxng", QuotaType.UNLIMITED, None) is None


def test_expiry_pressure_month_end(usage: UsageDB):
    # pressure = frac_quota_left * frac_time_elapsed — peaks when a mostly-full
    # monthly quota is about to reset; near zero when the quota is being used up.
    now = time.time()
    reset = usage.reset_time(QuotaType.MONTHLY, now)
    frac_time_elapsed = 1.0 - (reset - now) / (30 * 86400)

    full_quota_pressure = usage.expiry_pressure("exa", QuotaType.MONTHLY, 1000)
    # Fresh full quota => pressure equals elapsed-time fraction (behind pace).
    assert full_quota_pressure == pytest.approx(frac_time_elapsed, abs=0.02)

    # Burn 95% of quota => on pace, pressure should drop below the fresh case.
    usage.record("exa", success=True, credits=950)
    burned_pressure = usage.expiry_pressure("exa", QuotaType.MONTHLY, 1000)
    assert burned_pressure < full_quota_pressure

    # Non-monthly quotas never get pressure.
    assert usage.expiry_pressure("searxng", QuotaType.UNLIMITED, None) == 0.0


def test_quota_snapshot_reset_for_monthly(usage: UsageDB):
    snap = usage.quota_snapshot("exa", QuotaType.MONTHLY, 1000, True)
    assert snap.limit == 1000
    assert snap.reset_at is not None  # renewing monthly gets a reset estimate
    assert snap.remaining == 1000


# ---------------------------------------------------------------------------
# Health tracker
# ---------------------------------------------------------------------------

def test_health_degrades_and_recovers():
    h = HealthTracker(cooldown_s=0.05)
    assert h.state("exa").value == "healthy"
    h.record_failure("exa", "boom")
    assert h.state("exa").value == "degraded"  # first failure already penalizes
    h.record_success("exa")
    assert h.state("exa").value == "healthy"


def test_health_unavailable_then_cooldown_recovery():
    h = HealthTracker(cooldown_s=0.05)
    for _ in range(4):
        h.record_failure("you_mcp", "down")
    assert h.state("you_mcp").value == "unavailable"
    assert not h.is_usable("you_mcp")
    time.sleep(0.06)
    assert h.state("you_mcp").value == "degraded"  # cooldown gives another chance
    assert h.is_usable("you_mcp")


# ---------------------------------------------------------------------------
# Language / CN detection
# ---------------------------------------------------------------------------

def test_detect_language():
    assert detect_language("what is rust programming") == "en"
    assert detect_language("小米su7 评测") == "zh"
    assert detect_language("如何学习 Rust 编程语言") == "zh"


def test_china_relevance():
    assert china_relevance("微信公众号 排版工具") >= 0.7
    assert china_relevance("best python ide 2025") < 0.3
    assert china_relevance("bilibili 热门视频") >= 0.45


# ---------------------------------------------------------------------------
# Model schema sanity
# ---------------------------------------------------------------------------

def test_search_result_defaults():
    r = SearchResult(url="https://x.com/a", provider="exa")
    assert r.score == 0.0 and r.found_by == [] and r.retrieved_at > 0


def test_research_response_citations():
    resp = ResearchResponse(query="q", mode="max", providers_used=["exa"])
    assert resp.citations == [] and resp.synthesis is None
