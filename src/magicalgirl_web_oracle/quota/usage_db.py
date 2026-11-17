"""SQLite-backed usage & quota tracking.

Records per-call usage (provider, latency, success, mode), tracks rolling
quota consumption against renewing windows, and computes expiry_pressure
(monthly-quota providers get a small late-month priority boost).
Query text is stored only if store_query_text=true; otherwise a salted hash.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ..config.settings import get_settings
from ..models.schemas import QuotaSnapshot, QuotaType

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    endpoint TEXT NOT NULL DEFAULT '',
    ts REAL NOT NULL,
    requests INTEGER NOT NULL DEFAULT 1,
    success INTEGER NOT NULL DEFAULT 0,
    failure INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    estimated_credits REAL NOT NULL DEFAULT 1.0,
    mode TEXT NOT NULL DEFAULT '',
    query_hash TEXT NOT NULL DEFAULT '',
    query_text TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_usage_provider_ts ON usage(provider, ts);
CREATE TABLE IF NOT EXISTS quota_state (
    provider TEXT PRIMARY KEY,
    credits_used_this_window REAL NOT NULL DEFAULT 0,
    window_start REAL NOT NULL
);
"""


class UsageDB:
    def __init__(self, db_path: str | None = None) -> None:
        settings = get_settings()
        self.path = Path(db_path or settings.usage_db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.store_query_text = settings.store_query_text
        self.salt = settings.query_hash_salt
        # HTTP mode serves concurrent MCP clients: guard writes with a lock and
        # enable WAL so readers never block the writer.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                     timeout=10.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Window computation for quota types
    # ------------------------------------------------------------------

    @staticmethod
    def window_for(quota_type: QuotaType, now: float | None = None) -> tuple[float, float]:
        """Return (window_start, window_end) epoch seconds for the current window."""
        now = now or time.time()
        lt = time.localtime(now)
        if quota_type == QuotaType.DAILY:
            start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
            return start, start + 86400
        if quota_type in (QuotaType.MONTHLY, QuotaType.CALENDAR_MONTH):
            # Rolling 30-day window is a safe, conservative approximation of
            # "monthly" when the exact reset day is unknown.
            return now - 30 * 86400, now + 30 * 86400
        if quota_type == QuotaType.ONE_TIME:
            return 0.0, float("inf")
        return 0.0, float("inf")  # unlimited / unknown

    @staticmethod
    def reset_time(quota_type: QuotaType, now: float | None = None) -> float | None:
        now = now or time.time()
        if quota_type == QuotaType.DAILY:
            lt = time.localtime(now)
            tomorrow = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)) + 86400
            return tomorrow
        if quota_type in (QuotaType.MONTHLY, QuotaType.CALENDAR_MONTH):
            # Conservative estimate: end of current calendar month.
            lt = time.localtime(now)
            last_day = calendar.monthrange(lt.tm_year, lt.tm_mon)[1]
            return time.mktime((lt.tm_year, lt.tm_mon, last_day, 23, 59, 59, 0, 0, -1))
        return None

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, provider: str, *, endpoint: str = "", success: bool,
               latency_ms: int = 0, credits: float = 1.0, mode: str = "",
               query: str = "") -> None:
        qhash = hashlib.sha256((self.salt + query).encode()).hexdigest()[:16] if query else ""
        qtext = query if self.store_query_text else ""
        with self._lock:
            self._conn.execute(
                "INSERT INTO usage(provider, endpoint, ts, requests, success, failure,"
                " latency_ms, estimated_credits, mode, query_hash, query_text)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (provider, endpoint, time.time(), 1, int(success), int(not success),
                 latency_ms, credits, mode, qhash, qtext),
            )
            # Rolling credit counter for quota windows.
            self._conn.execute(
                """INSERT INTO quota_state(provider, credits_used_this_window, window_start)
                   VALUES(?,?,?)
                   ON CONFLICT(provider) DO UPDATE SET
                     credits_used_this_window = credits_used_this_window + ?""",
                (provider, credits, time.time(), credits),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def _credits_in_window(self, provider: str, quota_type: QuotaType,
                           now: float | None = None) -> float:
        start, _ = self.window_for(quota_type, now)
        row = self._conn.execute(
            "SELECT COALESCE(SUM(estimated_credits),0) FROM usage"
            " WHERE provider=? AND ts>=? AND success=1",
            (provider, start),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def remaining(self, provider: str, quota_type: QuotaType, limit: float | None,
                  now: float | None = None) -> float | None:
        if quota_type == QuotaType.UNLIMITED:
            return None  # infinite
        if limit is None or quota_type == QuotaType.UNKNOWN:
            return None
        used = self._credits_in_window(provider, quota_type, now)
        return max(0.0, float(limit - used))

    def quota_snapshot(self, provider: str, quota_type: QuotaType, limit: float | None,
                       renewing: bool, unit: str = "credits") -> QuotaSnapshot:
        now = time.time()
        remaining = self.remaining(provider, quota_type, limit, now)
        used = 0.0
        if quota_type == QuotaType.UNLIMITED:
            used = 0.0
        elif limit is not None and remaining is not None:
            used = limit - remaining
        return QuotaSnapshot(
            provider=provider,
            quota_type=quota_type,
            limit=limit,
            used=used,
            remaining=remaining,
            reset_at=self.reset_time(quota_type, now) if renewing else None,
            renewing=renewing,
            unit=unit,
        )

    def expiry_pressure(self, provider: str, quota_type: QuotaType, limit: float | None,
                        now: float | None = None) -> float:
        """0..1. High when a renewing monthly quota still has lots left near month end."""
        if quota_type not in (QuotaType.MONTHLY, QuotaType.CALENDAR_MONTH):
            return 0.0
        now = now or time.time()
        reset_at = self.reset_time(quota_type, now)
        if not reset_at:
            return 0.0
        remaining = self.remaining(provider, quota_type, limit, now)
        if remaining is None or limit in (None, 0):
            return 0.0
        frac_left = remaining / limit
        frac_time_left = max(0.0, min(1.0, (reset_at - now) / (30 * 86400)))
        # Boost when lots of quota left AND little time left.
        return round(frac_left * (1.0 - frac_time_left), 4)

    def stats_24h(self, provider: str) -> dict[str, Any]:
        since = time.time() - 86400
        row = self._conn.execute(
            "SELECT COUNT(*), AVG(latency_ms),"
            " SUM(CASE WHEN success=1 THEN 1 ELSE 0 END)"
            " FROM usage WHERE provider=? AND ts>=?",
            (provider, since),
        ).fetchone()
        total, avg_lat, succ = row if row else (0, None, 0)
        return {
            "calls_24h": total or 0,
            "avg_latency_ms": round(avg_lat, 1) if avg_lat is not None else None,
            "success_rate": round(succ / total, 3) if total else None,
        }

    def dump_json(self) -> str:
        return json.dumps({"db": str(self.path)})
