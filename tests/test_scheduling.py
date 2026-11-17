"""Explainable key scheduling strategies."""
from __future__ import annotations

import asyncio
from datetime import datetime as RealDatetime, timezone
from decimal import Decimal

import pytest

import magicalgirl_web_oracle.providers.key_pool as key_pool
from magicalgirl_web_oracle.providers.base import ProviderError
from magicalgirl_web_oracle.quota.accounts import AccountConfig
from magicalgirl_web_oracle.quota.ledger import AccountLedger
from magicalgirl_web_oracle.providers.key_pool import CredentialExecutor


def build_executor(tmp_path, settings, *, reset_a=28, reset_b=2, preferred=None, strategy="priority"):
    settings.scheduling_strategy = strategy
    settings.preferred_account = preferred or ""
    cfg = AccountConfig.model_validate({
        "accounts": [{"id": "a", "provider": "tavily", "label": "A"},
                     {"id": "b", "provider": "tavily", "label": "B"}],
        "pools": [
            {"id": "p-a", "account_id": "a", "unit": "credits", "limit": 10,
             "initial_used": 0, "free_allowance_confirmed": True,
             "window": {"kind": "monthly", "reset_day": reset_a, "timezone": "UTC"}},
            {"id": "p-b", "account_id": "b", "unit": "credits", "limit": 10,
             "initial_used": 0, "free_allowance_confirmed": True,
             "window": {"kind": "monthly", "reset_day": reset_b, "timezone": "UTC"}},
        ],
        "keys": [
            {"id": "k-a", "account_id": "a", "label": "A", "secret_env": "KEY_A",
             "pool_ids": ["p-a"], "priority": 1},
            {"id": "k-b", "account_id": "b", "label": "B", "secret_env": "KEY_B",
             "pool_ids": ["p-b"], "priority": 2},
        ],
    })
    ledger = AccountLedger(str(tmp_path / "accounts.sqlite3"))
    executor = CredentialExecutor(settings, cfg, ledger,
                                  environ={"KEY_A": "fake-a", "KEY_B": "fake-b"})
    return cfg, ledger, executor


def test_priority_is_default_order(tmp_path, settings):
    cfg, ledger, executor = build_executor(tmp_path, settings)
    try:
        ordered = executor._order_candidates("tavily", executor.keys_for("tavily"), executor.pools)
        assert [key.id for key in ordered] == ["k-a", "k-b"]
        assert executor.scheduling_info()["strategy"] == "priority"
    finally:
        ledger.close()


def test_preferred_account_orders_that_key_first(tmp_path, settings):
    cfg, ledger, executor = build_executor(tmp_path, settings,
                                           preferred="b", strategy="preferred_account")
    try:
        ordered = executor._order_candidates("tavily", executor.keys_for("tavily"), executor.pools)
        assert [key.id for key in ordered] == ["k-b", "k-a"]
        assert executor.scheduling_info()["preferred_account"] == "b"
    finally:
        ledger.close()


def test_balanced_prefers_less_consumed_key(tmp_path, settings):
    cfg, ledger, executor = build_executor(tmp_path, settings, strategy="balanced")
    try:
        call = ledger.reserve(cfg.keys[0], executor.pools, "tavily", "search", 4)
        ledger.dispatch(call)
        ledger.settle(call, 4)
        ordered = executor._order_candidates("tavily", executor.keys_for("tavily"), executor.pools)
        assert [key.id for key in ordered] == ["k-b", "k-a"]
    finally:
        ledger.close()


def test_expiry_prefers_earlier_reset(tmp_path, settings, monkeypatch):
    class FixedDatetime(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            return RealDatetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(key_pool, "datetime", FixedDatetime)
    cfg, ledger, executor = build_executor(tmp_path, settings, reset_a=1, reset_b=20,
                                           strategy="expiry")
    try:
        # Fixed at 2026-11-04: p-b resets on the 20th, p-a waits until December.
        ordered = executor._order_candidates("tavily", executor.keys_for("tavily"), executor.pools)
        assert [key.id for key in ordered] == ["k-b", "k-a"]
    finally:
        ledger.close()


def test_unknown_preferred_account_falls_back(tmp_path, settings):
    cfg, ledger, executor = build_executor(tmp_path, settings,
                                           preferred="missing", strategy="preferred_account")
    try:
        ordered = executor._order_candidates("tavily", executor.keys_for("tavily"), executor.pools)
        assert [key.id for key in ordered] == ["k-a", "k-b"]
    finally:
        ledger.close()


class _FakeProvider:
    def __init__(self):
        self.last_cost_reported = False

    async def search(self, query, **kwargs):
        from magicalgirl_web_oracle.models.schemas import SearchResult
        return [SearchResult(title="t", url="https://example.com", provider="tavily")]

    async def aclose(self):
        pass


def test_scheduling_reason_is_recorded_in_trace(tmp_path, settings):
    cfg, ledger, executor = build_executor(tmp_path, settings, strategy="balanced")
    try:
        asyncio.run(executor.execute("tavily", lambda snapshot: _FakeProvider(),
                                     "search", "query", max_results=1, mode="auto"))
        trace = ledger.traces(limit=1)[0]
        detail = ledger.trace_detail(trace["id"])
        reasons = [event["details"].get("reason") for event in detail["events"]]
        assert "scheduling_balanced" in reasons
    finally:
        ledger.close()
