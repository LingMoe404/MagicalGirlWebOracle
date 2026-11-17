"""Unified data models for MagicalGirlWebOracle.

Agent-facing schemas. Providers' private formats are normalized here —
downstream agents never need to understand Exa/Tavily/SearXNG wire formats.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

SearchMode = Literal["fast", "auto", "high", "max"]


class HealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ProviderKind(str, Enum):
    SEARCH = "search"
    FETCH = "fetch"
    RERANK = "rerank"


class QuotaType(str, Enum):
    DAILY = "daily"
    MONTHLY = "monthly"
    CALENDAR_MONTH = "calendar_month"
    ONE_TIME = "one_time"
    UNLIMITED = "unlimited"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Core result models
# ---------------------------------------------------------------------------


class SearchResult(BaseModel):
    """A single normalized search hit."""

    title: str = ""
    url: str
    snippet: str = ""
    content: str = ""  # full/extracted text when the provider supplied it
    published_at: str | None = None
    provider: str  # canonical name, e.g. "exa"
    score: float = 0.0  # normalized relevance 0..1 (provider-native if given)
    language: str | None = None
    retrieved_at: float = Field(default_factory=time.time)
    found_by: list[str] = Field(default_factory=list)  # all providers that hit this URL
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """Aggregated, deduplicated, optionally reranked search output."""

    query: str
    mode: SearchMode
    providers_used: list[str]
    results: list[SearchResult]
    dedup_stats: dict[str, int] = Field(default_factory=dict)
    usage: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class FetchedDocument(BaseModel):
    """Result of fetch_url routing."""

    url: str
    final_url: str | None = None
    title: str | None = None
    content: str  # markdown/plain text
    content_type: str = "markdown"
    provider: str  # which fetch backend produced this
    status: str = "ok"  # ok | failed | partial
    error: str | None = None
    retrieved_at: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    """A citation is ALWAYS grounded in a real URL from search results."""

    url: str
    title: str = ""
    snippet: str = ""
    provider: str | None = None
    source: Literal["search", "fetch"] = "search"


class ResearchResponse(BaseModel):
    """Evidence Pack for research-mode queries. No invented citations allowed."""

    query: str
    mode: SearchMode
    providers_used: list[str]
    key_sources: list[SearchResult] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    evidence: list[FetchedDocument] = Field(default_factory=list)
    synthesis: str | None = None  # optional LLM-side summary text, agent supplies
    conflicts: list[str] = Field(default_factory=list)
    cross_provider_agreement: dict[str, float] = Field(default_factory=dict)
    usage: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class CrawlPage(BaseModel):
    url: str
    content: str
    status: str = "ok"
    error: str | None = None


class CrawlResponse(BaseModel):
    root_url: str
    pages: list[CrawlPage]
    provider: str
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Status / quota models
# ---------------------------------------------------------------------------


class QuotaSnapshot(BaseModel):
    provider: str
    quota_type: QuotaType
    limit: float | None = None
    used: float = 0
    remaining: float | None = None
    reset_at: float | None = None  # epoch seconds; None = unknown/unlimited
    renewing: bool = False
    unit: str = "credits"  # "credits" (requests) | "usd" (dollar-denominated)


class ProviderStatus(BaseModel):
    provider: str
    kind: ProviderKind
    health: HealthState
    configured: bool  # has API key / URL?
    quota: QuotaSnapshot | None = None
    avg_latency_ms: float | None = None
    success_rate_24h: float | None = None
    last_error: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    live_balance: dict[str, Any] | None = None  # from official balance API, if any


class SystemStatus(BaseModel):
    providers: list[ProviderStatus]
    allow_paid_overage: bool = False
    version: str = "0.1.0"
    quota_alerts: list[dict[str, Any]] = Field(default_factory=list)
    quota_monitor: dict[str, Any] | None = None
    cache: dict[str, Any] | None = None
    scheduling: dict[str, Any] | None = None
