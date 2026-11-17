"""Opt-in response cache for search and fetch.

The cache is a separate SQLite file from the quota ledger. Entries are keyed by
a salted HMAC of the normalized request, so query text and URLs are not stored
as lookup keys. Search payloads redact the query; fetch payloads redact the
requested URL. Cached results are public web content, never credentials.

Design constraints:
  * disabled by default (``ORACLE_CACHE_TTL_SECONDS=0``);
  * never used for freshness-sensitive queries unless the caller explicitly
    bypasses the bypass with a dedicated parameter;
  * a cache failure must never fail the provider request;
  * a hit consumes no provider budget and is recorded in request tracing.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

CACHE_VERSION = 1
MAX_PAYLOAD_BYTES = 512 * 1024
FRESH_EN = re.compile(
    r"\b(latest|today|tonight|now|current|breaking|news|live|price|stock|"
    r"weather|score|release[sd]?|update[sd]?)\b",
    re.IGNORECASE,
)
FRESH_ZH = ("最新", "今天", "今日", "现在", "实时", "新闻", "股价", "汇率", "天气", "比分", "刚刚")


def _jsonable(value: Any, *, depth: int = 0) -> Any:
    """Return a stable JSON-safe value; unsupported objects are omitted."""
    if depth > 4:
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _jsonable(item, depth=depth + 1)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    return None


def is_fresh_query(query: str) -> bool:
    """Freshness-sensitive queries must not be served from a response cache."""
    text = query or ""
    return bool(FRESH_EN.search(text)) or any(marker in text for marker in FRESH_ZH)


class ResponseCache:
    def __init__(self, path: str | Path, *, max_entries: int = 500,
                 max_payload_bytes: int = MAX_PAYLOAD_BYTES) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_entries = max(1, int(max_entries))
        self.max_payload_bytes = max(1024, int(max_payload_bytes))
        self._lock = threading.RLock()
        self._misses = 0
        self.db = sqlite3.connect(str(self.path), isolation_level=None,
                                  check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        with self._lock:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS cache_meta("
                "name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS cache_entries("
                "key TEXT PRIMARY KEY, kind TEXT NOT NULL, created REAL NOT NULL, "
                "expires REAL NOT NULL, last_access REAL NOT NULL, "
                "hits INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL, "
                "payload TEXT NOT NULL)")
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS cache_expires ON cache_entries(expires)")
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS cache_kind_expires ON cache_entries(kind, expires)")
            self.db.execute(
                "INSERT OR IGNORE INTO cache_meta(name,value) VALUES('salt',?)",
                (secrets.token_hex(32),))
            self.salt = self.db.execute(
                "SELECT value FROM cache_meta WHERE name='salt'").fetchone()[0]

    # ------------------------------------------------------------------
    # Request material
    # ------------------------------------------------------------------

    @staticmethod
    def search_material(query: str, *, mode: str, max_results: int,
                        provider_override: str | None, opts: dict[str, Any]) -> dict:
        return {
            "kind": "search",
            "v": CACHE_VERSION,
            "query": query,
            "mode": mode,
            "max_results": max_results,
            "provider_override": provider_override or "",
            "opts": _jsonable(opts),
        }

    @staticmethod
    def fetch_material(url: str, *, force_provider: str | None,
                       skip: set[str] | None) -> dict:
        return {
            "kind": "fetch",
            "v": CACHE_VERSION,
            "url": url,
            "force_provider": force_provider or "",
            "skip": sorted(skip or ()),
        }

    def _key(self, material: dict[str, Any]) -> str:
        raw = json.dumps(material, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"))
        return hmac.new(bytes.fromhex(self.salt), raw.encode("utf-8"),
                        hashlib.sha256).hexdigest()

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------

    def lookup(self, kind: str, material: dict[str, Any]) -> tuple[dict | None, float | None]:
        key = self._key(material)
        now = time.time()
        try:
            with self._lock:
                row = self.db.execute(
                    "SELECT * FROM cache_entries WHERE key=? AND kind=?",
                    (key, kind)).fetchone()
                if row is None:
                    self._misses += 1
                    return None, None
                if row["expires"] <= now:
                    self.db.execute("DELETE FROM cache_entries WHERE key=?", (key,))
                    self._misses += 1
                    return None, None
                self.db.execute(
                    "UPDATE cache_entries SET hits=hits+1,last_access=? WHERE key=?",
                    (now, key))
                payload = json.loads(row["payload"])
                if not isinstance(payload, dict):
                    return None, None
                return payload, now - float(row["created"])
        except (sqlite3.Error, ValueError, TypeError) as exc:
            log.warning("cache lookup failed: %s", type(exc).__name__)
            self._misses += 1
            return None, None

    def store(self, kind: str, material: dict[str, Any], payload: dict[str, Any],
              ttl_seconds: int) -> bool:
        if ttl_seconds <= 0:
            return False
        try:
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return False
        encoded = raw.encode("utf-8")
        if len(encoded) > self.max_payload_bytes:
            return False
        key = self._key(material)
        now = time.time()
        try:
            with self._lock:
                self.db.execute(
                    "INSERT INTO cache_entries"
                    "(key,kind,created,expires,last_access,hits,bytes,payload) "
                    "VALUES(?,?,?,?,?,0,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET kind=excluded.kind,"
                    "created=excluded.created,expires=excluded.expires,"
                    "last_access=excluded.last_access,bytes=excluded.bytes,"
                    "payload=excluded.payload",
                    (key, kind, now, now + int(ttl_seconds), now,
                     len(encoded), raw))
                self.db.execute("DELETE FROM cache_entries WHERE expires<=?", (now,))
                count = self.db.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
                if count > self.max_entries:
                    self.db.execute(
                        "DELETE FROM cache_entries WHERE key IN ("
                        "SELECT key FROM cache_entries ORDER BY last_access ASC, created ASC LIMIT ?)",
                        (count - self.max_entries,))
            return True
        except sqlite3.Error as exc:
            log.warning("cache store failed: %s", type(exc).__name__)
            return False

    def stats(self) -> dict[str, Any]:
        try:
            with self._lock:
                entries = self.db.execute(
                    "SELECT COUNT(*) FROM cache_entries").fetchone()[0]
                stored_hits = self.db.execute(
                    "SELECT COALESCE(SUM(hits),0) FROM cache_entries").fetchone()[0]
            return {
                "enabled": True,
                "entries": entries,
                "hits": int(stored_hits),
                "misses": self._misses,
                "max_entries": self.max_entries,
            }
        except sqlite3.Error:
            return {"enabled": True, "entries": None, "hits": None,
                    "misses": self._misses, "max_entries": self.max_entries}

    def close(self) -> None:
        with self._lock:
            self.db.close()


def from_settings(settings) -> ResponseCache | None:
    """Build a cache only when the operator explicitly enabled it."""
    ttl = int(getattr(settings, "cache_ttl_seconds", 0) or 0)
    fetch_ttl = int(getattr(settings, "fetch_cache_ttl_seconds", 0) or 0)
    if ttl <= 0 and fetch_ttl <= 0:
        return None
    path = getattr(settings, "cache_path", "")
    if not path:
        return None
    try:
        return ResponseCache(
            path,
            max_entries=int(getattr(settings, "cache_max_entries", 500) or 500),
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        log.warning("response cache disabled: %s", type(exc).__name__)
        return None
