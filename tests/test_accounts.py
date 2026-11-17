"""Account identity and billing boundary regression tests; no real credentials."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from magicalgirl_web_oracle.quota.accounts import AccountConfig, BillingWindow, QuotaPool


def config_data():
    return {
        "accounts": [
            {"id": "tavily-a", "provider": "tavily", "label": "Tavily A", "owner_label": "Google-A"},
            {"id": "tavily-b", "provider": "tavily", "label": "Tavily B", "owner_label": "Google-B"},
        ],
        "pools": [
            {"id": "pool-a", "account_id": "tavily-a", "unit": "credits"},
            {"id": "pool-b", "account_id": "tavily-b", "unit": "credits"},
        ],
        "keys": [
            {"id": "key-a", "account_id": "tavily-a", "label": "A", "pool_ids": ["pool-a"], "secret_env": "TAVILY_A_KEY"},
            {"id": "key-b", "account_id": "tavily-b", "label": "B", "pool_ids": ["pool-b"], "secret_env": "TAVILY_B_KEY"},
        ],
    }


def test_two_accounts_and_shared_keys_do_not_duplicate_pools():
    data = config_data()
    data["keys"].append({**data["keys"][0], "id": "key-a-rotated", "secret_env": "TAVILY_A_NEW_KEY"})
    cfg = AccountConfig.model_validate(data)
    assert len(cfg.accounts) == 2 and len(cfg.pools) == 2 and len(cfg.keys) == 3
    assert cfg.keys[0].pool_ids == cfg.keys[2].pool_ids
    assert all(not p.free_allowance_confirmed and p.limit is None for p in cfg.pools)
    assert "TAVILY_A_KEY" not in cfg.model_dump_json()
    assert "TAVILY_A_KEY" not in repr(cfg)


@pytest.mark.parametrize("defect", ["duplicate_account", "duplicate_pool", "duplicate_key", "cross_account", "missing_pool", "duplicate_pool_ref", "duplicate_secret_ref", "unknown_account", "inline_secret", "mixed_unit"])
def test_invalid_account_graphs_rejected(defect):
    data = config_data()
    if defect.startswith("duplicate_") and defect.removeprefix("duplicate_") in ("account", "pool", "key"):
        name = defect.removeprefix("duplicate_") + "s"
        data[name].append(data[name][0].copy())
    elif defect == "cross_account":
        data["keys"][0]["pool_ids"] = ["pool-b"]
    elif defect == "missing_pool":
        data["keys"][0]["pool_ids"] = ["absent"]
    elif defect == "duplicate_pool_ref":
        data["keys"][0]["pool_ids"] = ["pool-a", "pool-a"]
    elif defect == "duplicate_secret_ref":
        data["keys"][1]["secret_env"] = data["keys"][0]["secret_env"]
    elif defect == "unknown_account":
        data["pools"][0]["account_id"] = "absent"
    elif defect == "inline_secret":
        data["keys"][0]["secret"] = "fake-do-not-accept"
    elif defect == "mixed_unit":
        data["pools"].append({"id": "usd", "account_id": "tavily-a", "unit": "usd"})
        data["keys"][0]["pool_ids"].append("usd")
    with pytest.raises(ValidationError):
        AccountConfig.model_validate(data)


@pytest.mark.parametrize("limit", [-1, "NaN", "Infinity", "-Infinity"])
def test_invalid_limits_rejected(limit):
    with pytest.raises(ValidationError):
        QuotaPool(id="pool", account_id="a", unit="usd", limit=limit, window=BillingWindow(kind="one_time"))


def test_decimal_limits_and_explicit_confirmation():
    pool = QuotaPool(id="pool", account_id="a", unit="usd", limit="0.007", window=BillingWindow(kind="one_time"))
    assert pool.limit == Decimal("0.007")
    assert not pool.free_allowance_confirmed
    with pytest.raises(ValidationError):
        QuotaPool(id="unknown", account_id="a", unit="usd", free_allowance_confirmed=True)


@pytest.mark.parametrize("fields", [
    {"kind": "monthly"}, {"kind": "rolling"}, {"kind": "daily", "reset_day": 1},
    {"kind": "monthly", "reset_day": 0}, {"timezone": "invalid/zone"},
    {"kind": "one_time", "rolling_days": 30},
])
def test_incomplete_or_conflicting_billing_semantics_rejected(fields):
    with pytest.raises(ValidationError):
        BillingWindow(**fields)


@pytest.mark.parametrize("now,expected_start,expected_end", [
    ("2026-11-01T00:00:00+08:00", "2026-10-31T16:00:00+00:00", "2026-11-30T16:00:00+00:00"),
    ("2026-12-31T23:59:59+08:00", "2026-11-30T16:00:00+00:00", "2026-12-31T16:00:00+00:00"),
])
def test_monthly_window_in_account_timezone(now, expected_start, expected_end):
    window = BillingWindow(kind="monthly", reset_day=1, timezone="Asia/Shanghai")
    start, end = window.bounds(datetime.fromisoformat(now))
    assert start == datetime.fromisoformat(expected_start)
    assert end == datetime.fromisoformat(expected_end)


@pytest.mark.parametrize("year,day", [(2024, 29), (2025, 28)])
def test_bill_day_31_clamps_and_recovers_after_february(year, day):
    window = BillingWindow(kind="monthly", reset_day=31)
    start, end = window.bounds(datetime(year, 2, day, tzinfo=timezone.utc))
    assert start == datetime(year, 2, day, tzinfo=timezone.utc)
    assert end == datetime(year, 3, 31, tzinfo=timezone.utc)
    assert window.bounds(end - timedelta(microseconds=1)) == (start, end)
    assert window.bounds(end)[0] == end


def test_before_custom_bill_day_uses_previous_month():
    window = BillingWindow(kind="monthly", reset_day=15)
    assert window.bounds(datetime(2026, 1, 1, tzinfo=timezone.utc)) == (
        datetime(2025, 12, 15, tzinfo=timezone.utc), datetime(2026, 1, 15, tzinfo=timezone.utc))


@pytest.mark.parametrize("date,hours", [("2026-03-08T12:00:00+00:00", 23), ("2026-11-01T12:00:00+00:00", 25)])
def test_daily_window_follows_dst_instead_of_fixed_86400_seconds(date, hours):
    start, end = BillingWindow(kind="daily", timezone="America/New_York").bounds(datetime.fromisoformat(date))
    assert (end - start).total_seconds() == hours * 3600


def test_unknown_and_nonrenewing_windows_have_no_invented_reset():
    now = datetime(2026, 11, 4, tzinfo=timezone.utc)
    for kind in ("unknown", "one_time", "unlimited"):
        assert BillingWindow(kind=kind).bounds(now) == (None, None)
    assert BillingWindow(kind="rolling", rolling_days=30).bounds(now) == (now - timedelta(days=30), None)
    with pytest.raises(ValueError, match="timezone-aware"):
        BillingWindow().bounds(datetime(2026, 11, 4))
