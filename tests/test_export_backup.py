"""Sanitized export and SQLite backup helpers."""
from __future__ import annotations

import json
import sqlite3

import pytest
from click.testing import CliRunner

from magicalgirl_web_oracle.cli.main import cli
from magicalgirl_web_oracle.providers.key_pool import CredentialExecutor
from magicalgirl_web_oracle.quota.export import (
    backup_database,
    export_rows,
    month_bounds,
    rows_csv,
    verify_backup,
)
from magicalgirl_web_oracle.quota.ledger import AccountLedger

from test_key_pool import account_config


def make_ledger(path, settings):
    cfg = account_config(shared=False, limit=10)
    ledger = AccountLedger(str(path))
    executor = CredentialExecutor(settings, cfg, ledger,
                                  environ={"KEY_A": "fake-a", "KEY_B": "fake-b"})
    call = ledger.reserve(cfg.keys[0], executor.pools, "tavily", "search", 2)
    ledger.dispatch(call)
    ledger.settle(call, 2, latency_ms=12)
    return cfg, ledger


def test_export_rows_are_sanitized(tmp_path, settings):
    cfg, ledger = make_ledger(tmp_path / "usage.accounts.sqlite3", settings)
    try:
        rows = export_rows(ledger.path, -1e30, 1e30)
        assert len(rows) == 1
        row = rows[0]
        assert row["provider"] == "tavily"
        assert row["account_id"] == "a"
        assert row["key_id"] == "k-a"
        assert row["pool_id"] == "p-a"
        assert row["charged"] == "2.000000"
        assert "query" not in row and "url" not in row
        text = rows_csv(rows)
        assert "call_id" in text and "charged_units" in text
        assert "fake-a" not in text
    finally:
        ledger.close()


def test_month_bounds_validation():
    start, end = month_bounds("2026-11")
    assert end > start
    with pytest.raises(ValueError):
        month_bounds("2026-13")
    with pytest.raises(ValueError):
        month_bounds("2026-1")


def test_backup_and_verify(tmp_path, settings):
    cfg, ledger = make_ledger(tmp_path / "usage.accounts.sqlite3", settings)
    try:
        output = tmp_path / "backup" / "usage.accounts.sqlite3"
        manifest = backup_database(ledger.path, output)
        assert manifest["integrity_check"] == "ok"
        assert manifest["counts"]["calls"] == 1
        assert manifest["counts"]["credentials"] == 2
        assert output.exists()
        result = verify_backup(output)
        assert result["ok"] is True
        assert result["schema_version"] == 5
        assert result["counts"]["allocations"] == 1
        manifest_path = output.with_name(output.name + ".manifest.json")
        assert json.loads(manifest_path.read_text(encoding="utf-8"))["sha256"] == result["sha256"]
    finally:
        ledger.close()


def test_backup_refuses_overwrite(tmp_path, settings):
    cfg, ledger = make_ledger(tmp_path / "usage.accounts.sqlite3", settings)
    try:
        output = tmp_path / "backup.sqlite3"
        backup_database(ledger.path, output)
        with pytest.raises(FileExistsError):
            backup_database(ledger.path, output)
    finally:
        ledger.close()


def test_verify_rejects_non_sqlite(tmp_path):
    broken = tmp_path / "broken.sqlite3"
    broken.write_bytes(b"not a sqlite database")
    with pytest.raises(sqlite3.DatabaseError):
        verify_backup(broken)


def test_cli_export_and_backup(tmp_path, settings, monkeypatch):
    settings.usage_db_path = str(tmp_path / "usage.sqlite3")
    cfg, ledger = make_ledger(tmp_path / "usage.accounts.sqlite3", settings)
    ledger.close()
    monkeypatch.setattr("magicalgirl_web_oracle.cli.main.get_settings", lambda: settings)
    runner = CliRunner()
    result = runner.invoke(cli, ["export", "--all", "--format", "json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["rows"][0]["provider"] == "tavily"
    backup_path = tmp_path / "cli-backup.sqlite3"
    result = runner.invoke(cli, ["backup", "--output", str(backup_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["integrity_check"] == "ok"
    result = runner.invoke(cli, ["verify-backup", str(backup_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["ok"] is True
