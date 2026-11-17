"""Sanitized history export and SQLite backup helpers (no credentials)."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

SCALE = 1_000_000


def account_ledger_path(settings) -> Path:
    return Path(settings.usage_db_path).with_suffix(".accounts.sqlite3")


def month_bounds(month: str) -> tuple[float, float]:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
        raise ValueError("invalid month; expected YYYY-MM")
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    end = datetime(start.year + (start.month == 12), start.month % 12 + 1, 1,
                   tzinfo=timezone.utc)
    return start.timestamp(), end.timestamp()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(str(path))
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _schema_version(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT version FROM account_schema").fetchone()
    if row is None:
        raise ValueError("not an account ledger (account_schema missing)")
    version = int(row[0])
    if version != 5:
        raise ValueError(f"unsupported account ledger schema: {version}")
    return version


def export_rows(db_path: str | Path, start: float, end: float,
                *, limit: int = 100_000) -> list[dict[str, Any]]:
    con = _connect_readonly(Path(db_path))
    try:
        _schema_version(con)
        rows = con.execute(
            "SELECT c.id AS call_id,c.key_id,c.ts,c.provider,c.endpoint,c.state,c.success,"
            "c.error,c.latency_ms,cr.account_id,a.pool_id,a.reserved,a.charged,p.identity "
            "FROM calls c LEFT JOIN credentials cr ON cr.id=c.key_id "
            "LEFT JOIN allocations a ON a.call_id=c.id "
            "LEFT JOIN pools p ON p.id=a.pool_id "
            "WHERE c.ts>=? AND c.ts<? ORDER BY c.ts,c.id,a.pool_id LIMIT ?",
            (start, end, max(1, min(int(limit), 200_000)))).fetchall()
    finally:
        con.close()
    result = []
    for row in rows:
        unit = ""
        if row["identity"]:
            try:
                unit = json.loads(row["identity"]).get("unit", "")
            except ValueError:
                unit = ""
        reserved = int(row["reserved"] or 0)
        charged = int(row["charged"] or 0)
        result.append({
            "call_id": row["call_id"],
            "ts": datetime.fromtimestamp(row["ts"], timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": row["provider"],
            "endpoint": row["endpoint"],
            "state": row["state"],
            "success": None if row["success"] is None else bool(row["success"]),
            "error_code": row["error"] or "",
            "latency_ms": int(row["latency_ms"] or 0),
            "key_id": row["key_id"],
            "account_id": row["account_id"] or "",
            "pool_id": row["pool_id"] or "",
            "unit": unit,
            "reserved": f"{reserved / SCALE:.6f}",
            "charged": f"{charged / SCALE:.6f}",
            "reserved_units": reserved,
            "charged_units": charged,
        })
    return result


def rows_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    if target.exists():
        raise FileExistsError(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _counts(con: sqlite3.Connection) -> dict[str, int]:
    counts = {}
    for table in ("pools", "credentials", "calls", "allocations", "balance_snapshots"):
        counts[table] = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    row = con.execute("SELECT revision FROM managed_config").fetchone()
    counts["config_revision"] = int(row[0]) if row else 0
    return counts


def backup_database(source: str | Path, output: str | Path) -> dict[str, Any]:
    source_path = Path(source)
    output_path = Path(output)
    if output_path.exists():
        raise FileExistsError(str(output_path))
    output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    src = _connect_readonly(source_path)
    dst = sqlite3.connect(str(output_path))
    try:
        with dst:
            src.backup(dst)
        dst.execute("PRAGMA journal_mode=delete")
        integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
        schema_version = _schema_version(dst)
        counts = _counts(dst)
    finally:
        dst.close()
        src.close()
    if integrity != "ok":
        output_path.unlink(missing_ok=True)
        raise ValueError(f"backup integrity check failed: {integrity}")
    try:
        os.chmod(output_path, 0o600)
    except OSError:
        pass
    manifest = {
        "source": source_path.name,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "schema_version": schema_version,
        "counts": counts,
        "bytes": output_path.stat().st_size,
        "sha256": _sha256(output_path),
        "integrity_check": integrity,
        "note": "Encrypted credentials only; ORACLE_VAULT_KEY is not included.",
    }
    manifest_path = output_path.with_name(output_path.name + ".manifest.json")
    if manifest_path.exists():
        output_path.unlink(missing_ok=True)
        raise FileExistsError(str(manifest_path))
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    try:
        os.chmod(manifest_path, 0o600)
    except OSError:
        pass
    return manifest


def verify_backup(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    con = _connect_readonly(target)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        schema_version = _schema_version(con)
        counts = _counts(con)
    finally:
        con.close()
    result = {
        "path": str(target),
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "schema_version": schema_version,
        "counts": counts,
        "integrity_check": integrity,
        "ok": integrity == "ok",
    }
    if not result["ok"]:
        raise ValueError(f"backup integrity check failed: {integrity}")
    return result
