"""Secret-free contracts for multi-account quota configuration.

These contracts deliberately do not replace the live provider registry yet.
Limits and billing windows must be confirmed independently for each account.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone, time as clock_time
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")]
Provider = Literal["exa", "tavily", "firecrawl", "jina", "you"]
Amount = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class BillingWindow(Contract):
    """Explicit billing semantics; all finite windows are half-open [start, end)."""

    kind: Literal["unknown", "daily", "monthly", "rolling", "one_time", "unlimited"] = "unknown"
    timezone: str = "UTC"
    reset_day: int | None = Field(default=None, ge=1, le=31)
    reset_time: str = Field(default='00:00:00',pattern=r'^\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$')
    rolling_days: int | None = Field(default=None, ge=1, le=366)

    @model_validator(mode="after")
    def validate_window(self) -> BillingWindow:
        clock_time.fromisoformat(self.reset_time)
        if self.kind!='monthly' and self.reset_time!='00:00:00':
            raise ValueError('reset_time is only supported for monthly billing')
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown billing timezone") from exc
        if (self.kind == "monthly") != (self.reset_day is not None):
            raise ValueError("monthly billing requires reset_day; other windows forbid it")
        if (self.kind == "rolling") != (self.rolling_days is not None):
            raise ValueError("rolling billing requires rolling_days; other windows forbid it")
        return self

    def bounds(self, now: datetime) -> tuple[datetime | None, datetime | None]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if self.kind in ("unknown", "one_time", "unlimited"):
            return None, None
        if self.kind == "rolling":
            # A rolling window has no single future reset instant.
            return now.astimezone(timezone.utc) - timedelta(days=self.rolling_days), None
        local = now.astimezone(ZoneInfo(self.timezone))
        if self.kind == "daily":
            start = local.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        else:
            def boundary(year: int, month: int) -> datetime:
                day = min(self.reset_day, calendar.monthrange(year, month)[1])
                at=clock_time.fromisoformat(self.reset_time)
                return datetime(year,month,day,at.hour,at.minute,at.second,at.microsecond,tzinfo=local.tzinfo)

            start = boundary(local.year, local.month)
            if local < start:
                month = local.month - 1 or 12
                start = boundary(local.year - (local.month == 1), month)
            month = start.month % 12 + 1
            end = boundary(start.year + (start.month == 12), month)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


class ProviderAccount(Contract):
    id: Identifier
    provider: Provider
    label: str = Field(min_length=1, max_length=100)
    owner_label: str = Field(default="", max_length=100)


class QuotaPool(Contract):
    id: Identifier
    account_id: Identifier
    unit: Literal["usd", "credits", "tokens", "requests"]
    limit: Amount | None = None
    initial_used: Amount | None = None
    window: BillingWindow = Field(default_factory=BillingWindow)
    # Confirmation means the administrator has established the free allowance;
    # it does not mean a local estimate is an official live balance.
    free_allowance_confirmed: bool = False

    @model_validator(mode="after")
    def validate_limit(self) -> QuotaPool:
        if self.window.kind in ("unknown", "unlimited"):
            if self.limit is not None:
                raise ValueError("unknown/unlimited pools cannot have a numeric limit")
        elif self.limit is None:
            raise ValueError("a finite pool requires a limit")
        if self.window.kind == "unknown" and self.free_allowance_confirmed:
            raise ValueError("unknown allowance cannot be confirmed")
        return self


class ApiKeyRef(Contract):
    id: Identifier
    account_id: Identifier
    label: str = Field(min_length=1, max_length=100)
    pool_ids: tuple[Identifier, ...] = Field(min_length=1)
    # Only a reference is accepted here. Key material belongs in the private
    # credential store or the process environment, never this public config.
    secret_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,127}$", exclude=True, repr=False)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10000)


class AccountConfig(Contract):
    version: Literal[1] = 1
    accounts: tuple[ProviderAccount, ...] = ()
    pools: tuple[QuotaPool, ...] = ()
    keys: tuple[ApiKeyRef, ...] = ()

    @model_validator(mode="after")
    def validate_graph(self) -> AccountConfig:
        for collection in (self.accounts, self.pools, self.keys):
            if len({item.id for item in collection}) != len(collection):
                raise ValueError("duplicate identifier in account configuration")
        accounts = {a.id: a for a in self.accounts}
        pools = {p.id: p for p in self.pools}
        seen_refs: set[str] = set()
        for pool in self.pools:
            if pool.account_id not in accounts:
                raise ValueError("quota pool references an unknown account")
            expected_unit = {"exa":"usd","tavily":"credits","firecrawl":"credits","jina":"tokens"}.get(accounts[pool.account_id].provider)
            if expected_unit and pool.unit != expected_unit:
                raise ValueError("quota pool unit does not match its provider")
        for key in self.keys:
            if key.account_id not in accounts:
                raise ValueError("key references an unknown account")
            if len(set(key.pool_ids)) != len(key.pool_ids):
                raise ValueError("key references the same pool more than once")
            if key.secret_env in seen_refs:
                raise ValueError("credential reference is already assigned to a key")
            seen_refs.add(key.secret_env)
            linked = [pools.get(pool_id) for pool_id in key.pool_ids]
            if any(pool is None or pool.account_id != key.account_id for pool in linked):
                raise ValueError("key pools must exist and belong to the same account")
            if len({pool.unit for pool in linked}) != 1:
                raise ValueError("a key's pools must use the same quota unit")
        return self
