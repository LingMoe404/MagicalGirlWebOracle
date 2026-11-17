"""CLI: magicalgirl-web {search,research,fetch,crawl,status,doctor}.

Each command runs its whole lifecycle (work + cleanup) inside ONE
asyncio.run() — on Windows, closing httpx clients from a second loop raises
RuntimeError('Event loop is closed').
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

import click

from ..config.settings import get_settings
from ..providers.base import ProviderError
from ..quality_eval import (
    evaluate_mcp_samples,
    evaluate_samples,
    load_samples,
    render_markdown,
)
from ..quota.export import (
    account_ledger_path,
    backup_database,
    export_rows,
    month_bounds,
    rows_csv,
    verify_backup,
    write_text,
)
from ..router.oracle import Oracle


def _run_cli(coro_fn, *args, **kwargs):
    """Run an async command function to completion in one event loop."""

    async def runner():
        oracle = None
        try:
            oracle = Oracle(settings=get_settings())
            return await coro_fn(oracle, *args, **kwargs)
        finally:
            if oracle is not None:
                await oracle.aclose()

    return asyncio.run(runner())


def _echo_response(resp, as_json: bool) -> None:
    if as_json:
        click.echo(resp.model_dump_json(indent=2))
        return
    if hasattr(resp, "results"):
        click.secho(f"mode={resp.mode} providers={','.join(resp.providers_used)} "
                    f"dedup={resp.dedup_stats.get('raw', 0)}->{resp.dedup_stats.get('deduped', 0)}",
                    fg="cyan", err=True)
        for i, r in enumerate(resp.results, 1):
            agree = r.metadata.get("cross_provider_agreement")
            agree_s = f" [agreement {agree}]" if agree else ""
            click.secho(f"{i}. {r.title}", fg="green")
            click.echo(f"   {r.url}")
            snip = (r.snippet or r.content[:200]).replace("\n", " ")
            click.echo(f"   {snip[:220]}")
            click.echo(f"   by: {','.join(r.found_by)}{agree_s}")
        for n in resp.notes:
            click.secho(f"note: {n}", fg="yellow", err=True)
    else:
        click.echo(resp.model_dump_json(indent=2))


@click.group()
@click.version_option(package_name="magicalgirl-web-oracle", prog_name="magicalgirl-web")
def cli() -> None:
    """MagicalGirlWebOracle — zero-cost unified web search/fetch/research for agents."""


@cli.command()
@click.argument("query")
@click.option("--mode", "-m", type=click.Choice(["fast", "auto", "high", "max"]),
              default=None, help="Search mode (default from settings).")
@click.option("--provider", "-p", "provider_override", default=None,
              help="Debug: force a specific provider (exa/tavily/searxng/you_mcp).")
@click.option("--limit", "-n", default=10, show_default=True)
@click.option("--bypass-cache", is_flag=True, help="Force a fresh provider request.")
@click.option("--json", "as_json", is_flag=True, help="Emit full JSON.")
def search(query: str, mode: str | None, provider_override: str | None,
           limit: int, bypass_cache: bool, as_json: bool) -> None:
    """Search the web with automatic provider routing."""

    async def cmd(oracle: Oracle):
        return await oracle.web_search(query, mode=mode, max_results=limit,
                                       provider_override=provider_override,
                                       bypass_cache=bypass_cache)

    _echo_response(_run_cli(cmd), as_json)


@cli.command()
@click.argument("query")
@click.option("--limit", "-n", default=12)
@click.option("--fetch-top", default=3, show_default=True,
              help="Fetch full text of top N sources for the evidence pack.")
@click.option("--bypass-cache", is_flag=True, help="Force fresh search and fetch requests.")
@click.option("--json", "as_json", is_flag=True)
def research(query: str, limit: int, fetch_top: int, bypass_cache: bool,
             as_json: bool) -> None:
    """Deep research: multi-provider + evidence pack with real citations."""

    async def cmd(oracle: Oracle):
        resp = await oracle.web_research(query, max_results=limit, fetch_top=fetch_top,
                                         bypass_cache=bypass_cache)
        if as_json:
            click.echo(resp.model_dump_json(indent=2))
            return
        click.secho(f"providers={','.join(resp.providers_used)}", fg="cyan", err=True)
        click.secho("== Key sources ==", bold=True)
        for i, r in enumerate(resp.key_sources, 1):
            click.secho(f"{i}. {r.title or r.url}", fg="green")
            click.echo(f"   {r.url}  (by {','.join(r.found_by)})")
        click.secho("== Citations ==", bold=True)
        for c in resp.citations:
            click.echo(f"- [{c.source}:{c.provider}] {c.url}")
        for ev in resp.evidence:
            click.secho(f"== Evidence: {ev.url} (via {ev.provider}) ==", bold=True)
            click.echo(ev.content[:2000])
        for n in resp.notes:
            click.secho(f"note: {n}", fg="yellow", err=True)

    _run_cli(cmd)


@cli.command()
@click.argument("url")
@click.option("--provider", "-p", "force_provider", default=None,
              help="Debug: force fetch backend (light_http/jina_reader/crawl4ai/firecrawl).")
@click.option("--bypass-cache", is_flag=True, help="Force a fresh fetch request.")
@click.option("--json", "as_json", is_flag=True)
def fetch(url: str, force_provider: str | None, bypass_cache: bool,
          as_json: bool) -> None:
    """Fetch a URL through the escalation ladder (light->jina->crawl4ai->firecrawl)."""

    async def cmd(oracle: Oracle):
        return await oracle.fetch_url(url, force_provider=force_provider,
                                      bypass_cache=bypass_cache)

    doc = _run_cli(cmd)
    if as_json:
        click.echo(doc.model_dump_json(indent=2))
        return
    click.secho(f"provider={doc.provider} type={doc.content_type}", fg="cyan", err=True)
    if doc.title:
        click.secho(doc.title, bold=True)
    click.echo(doc.content)


@cli.command()
@click.argument("url")
@click.option("--max-pages", default=10, show_default=True)
@click.option("--max-depth", default=2, show_default=True)
@click.option("--json", "as_json", is_flag=True)
def crawl(url: str, max_pages: int, max_depth: int, as_json: bool) -> None:
    """Crawl a site (Crawl4AI first, Firecrawl fallback)."""

    async def cmd(oracle: Oracle):
        return await oracle.crawl_site(url, max_pages=max_pages, max_depth=max_depth)

    resp = _run_cli(cmd)
    if as_json:
        click.echo(resp.model_dump_json(indent=2))
        return
    click.secho(f"provider={resp.provider} pages={len(resp.pages)}", fg="cyan", err=True)
    for p in resp.pages:
        click.secho(f"== {p.url} ==", bold=True)
        click.echo(p.content[:1500])


@cli.command("export")
@click.option("--month", default=None, help="UTC month, YYYY-MM (default: current month).")
@click.option("--all", "all_rows", is_flag=True, help="Export all stored calls.")
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]), default="csv")
@click.option("--output", "-o", default="-", help="File path, or - for stdout.")
def export_cmd(month: str | None, all_rows: bool, fmt: str, output: str) -> None:
    """Export sanitized call history (no query, URL, or credential fields)."""
    if all_rows and month:
        raise click.ClickException("use either --month or --all, not both")
    settings = get_settings()
    db_path = account_ledger_path(settings)
    if all_rows:
        start, end = -1e30, 1e30
        label = "all"
    else:
        label = month or datetime.now(timezone.utc).strftime("%Y-%m")
        try:
            start, end = month_bounds(label)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from None
    try:
        rows = export_rows(db_path, start, end)
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from None
    if fmt == "json":
        text = json.dumps({"schema_version": 5, "month": label, "rows": rows},
                          ensure_ascii=False, indent=2)
    else:
        text = rows_csv(rows)
    if output == "-":
        click.echo(text)
        return
    try:
        write_text(output, text)
    except FileExistsError:
        raise click.ClickException(f"output already exists: {output}") from None
    click.echo(f"wrote {len(rows)} row(s) to {output}", err=True)


@cli.command("backup")
@click.option("--output", "-o", default=None,
              help="Destination SQLite file (default: backup/usage.accounts.<UTC>.sqlite3).")
@click.option("--db", "db_path", default=None, help="Source account ledger path.")
def backup_cmd(output: str | None, db_path: str | None) -> None:
    """Copy the account ledger with the SQLite backup API and write a manifest."""
    settings = get_settings()
    source = Path(db_path) if db_path else account_ledger_path(settings)
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        output = str(Path("backup") / f"usage.accounts.{stamp}.sqlite3")
    try:
        manifest = backup_database(source, output)
    except FileExistsError as exc:
        raise click.ClickException(f"refusing to overwrite existing path: {exc}") from None
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(json.dumps(manifest, ensure_ascii=False, indent=2))


@cli.command("verify-backup")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
def verify_backup_cmd(path: str) -> None:
    """Verify a backup's integrity, schema version and row counts."""
    try:
        result = verify_backup(path)
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(json.dumps(result, ensure_ascii=False, indent=2))


@cli.command("quality-eval")
@click.option("--samples", type=click.Path(exists=True, dir_okay=False), default=None,
              help="YAML sample file (default: bundled public samples).")
@click.option("--mode", type=click.Choice(["fast", "auto", "high", "max"]), default="auto")
@click.option("--max-results", default=5, show_default=True, type=click.IntRange(1, 20))
@click.option("--max-samples", default=6, show_default=True, type=click.IntRange(1, 20))
@click.option("--live", is_flag=True, help="Actually call providers and consume free quota.")
@click.option("--mcp-url", default=None,
              help="Evaluate a deployed MCP endpoint instead of the local Oracle.")
@click.option("--token-env", default="MCP_AUTH_TOKEN", show_default=True,
              help="Environment variable holding the MCP Bearer token (never printed).")
@click.option("--allow-no-token", is_flag=True,
              help="Allow an explicitly unauthenticated MCP endpoint (LAN-only deployments).")
@click.option("--format", "fmt", type=click.Choice(["markdown", "json"]), default="markdown")
@click.option("--output", "-o", default="-", help="File path, or - for stdout.")
def quality_eval_cmd(samples: str | None, mode: str, max_results: int,
                     max_samples: int, live: bool, mcp_url: str | None,
                     token_env: str, allow_no_token: bool, fmt: str, output: str) -> None:
    """Run a fixed-sample retrieval-quality check (domain/keyword/latency).

    Without --live this only validates the sample file; it never calls a provider."""
    try:
        selected = load_samples(samples)[:max_samples]
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from None
    if not live:
        click.echo(f"validated {len(selected)} sample(s); no provider calls made. "
                   "Use --live to run and consume free quota.", err=True)
        return
    if mcp_url:
        token = os.getenv(token_env, "")
        if not token and not allow_no_token:
            raise click.ClickException(
                f"Bearer token env {token_env} is empty; use --allow-no-token for a LAN-only endpoint")
        report = asyncio.run(evaluate_mcp_samples(
            mcp_url, token, selected, mode=mode, max_results=max_results))
    else:
        settings = get_settings()
        if settings.allow_paid_overage:
            raise click.ClickException("ALLOW_PAID_OVERAGE must remain false for quality evaluation")

        async def cmd(oracle: Oracle):
            return await evaluate_samples(
                oracle, selected, mode=mode, max_results=max_results, bypass_cache=True)

        report = _run_cli(cmd)
    text = (json.dumps(report, ensure_ascii=False, indent=2)
            if fmt == "json" else render_markdown(report))
    if output == "-":
        click.echo(text)
        return
    try:
        write_text(output, text)
    except FileExistsError:
        raise click.ClickException(f"output already exists: {output}") from None
    click.echo(f"wrote quality report to {output}", err=True)


@cli.command("status")
@click.option("--json", "as_json", is_flag=True)
def status_cmd(as_json: bool) -> None:
    """Provider health / quota / usage table. Never shows API keys."""

    async def cmd(oracle: Oracle):
        return oracle.status()

    st = _run_cli(cmd)
    if as_json:
        click.echo(st.model_dump_json(indent=2))
        return
    click.secho(f"allow_paid_overage={st.allow_paid_overage} (HARD RULE: always false)",
                fg="green" if not st.allow_paid_overage else "red")
    click.echo(f"{'Provider':<14} {'Kind':<8} {'Health':<12} {'Conf':<5} "
               f"{'QuotaType':<10} {'Used':>8} {'Limit':>8} {'Reset':<8} "
               f"{'Lat ms':>7} {'Succ24h':>7}")
    for p in st.providers:
        q = p.quota
        reset = "-"
        if q and q.reset_at:
            import time as _t
            h = max(0, (q.reset_at - _t.time()) / 3600)
            reset = f"{h:.0f}h"
        qtype = q.quota_type.value if q else "-"
        unit = q.unit if q else "credits"
        def fmt(v):
            if v is None:
                return "∞"
            return f"${v:.4f}" if unit == "usd" else f"{v:g}"
        used = fmt(q.used) if q else "0"
        limit = fmt(q.limit) if q else "-"
        lat = str(p.avg_latency_ms) if p.avg_latency_ms is not None else "-"
        succ = str(p.success_rate_24h) if p.success_rate_24h is not None else "-"
        click.echo(
            f"{p.provider:<14} {p.kind.value:<8} {p.health.value:<12} "
            f"{str(p.configured):<5} {qtype:<10} {used:>8} {limit:>8} "
            f"{reset:<8} {lat:>7} {succ:>7}"
        )
    for alert in st.quota_alerts:
        click.echo(f"quota_alert kind={alert['kind']} severity={alert['severity']} count={alert['count']}")
    if st.quota_monitor is not None:
        click.echo(f"quota_monitor running={st.quota_monitor['running']} interval={st.quota_monitor['interval_seconds']}s")
    if st.scheduling:
        click.echo(f"scheduling strategy={st.scheduling.get('strategy')} "
                   f"preferred_account={st.scheduling.get('preferred_account') or '-'}")
    if st.cache:
        click.echo(f"cache enabled={st.cache.get('enabled')} entries={st.cache.get('entries')} "
                   f"hits={st.cache.get('hits')} search_ttl={st.cache.get('search_ttl_seconds')}s "
                   f"fetch_ttl={st.cache.get('fetch_ttl_seconds')}s")


@cli.command()
@click.option("--json", "as_json", is_flag=True,
              help="Machine-readable output (for automated deployment checks).")
def doctor(as_json: bool) -> None:
    """Connectivity & config diagnostics (masks all secrets)."""
    settings = get_settings()
    result: dict = {"settings": settings.masked_summary(), "providers": {},
                    "probes": {}, "balances": {}, "ok": True}

    if not as_json:
        click.secho("== Settings (masked) ==", bold=True)
        click.echo(json.dumps(settings.masked_summary(), indent=2, ensure_ascii=False))
        click.secho("== Provider checks ==", bold=True)

    async def cmd(oracle: Oracle):
        for name, prov in oracle.registry.all.items():
            state = oracle.health.state(name)
            result["providers"][name] = {"configured": prov.configured,
                                         "health": state.value}
            if not as_json:
                click.echo(f"{name:<14} configured={prov.configured} health={state.value}")

        if not as_json:
            click.secho("== Live probes ==", bold=True)

        searx = oracle.registry.search.get("searxng")
        if searx.configured:
            try:
                r = await searx.search("test", max_results=1, mode="fast")
                result["probes"]["searxng"] = {"ok": True, "results": len(r)}
                if not as_json:
                    click.secho(f"searxng: OK ({len(r)} result(s))", fg="green")
            except ProviderError as e:
                result["probes"]["searxng"] = {"ok": False, "error": str(e)[:200]}
                if not as_json:
                    click.secho(f"searxng: FAIL ({e})", fg="red")
        else:
            result["probes"]["searxng"] = {"ok": False, "error": "not configured"}
            if not as_json:
                click.secho("searxng: not configured (set SEARXNG_URL)", fg="yellow")

        c4 = oracle.registry.fetch.get("crawl4ai")
        if c4.configured:
            try:
                d = await c4.fetch("https://example.com")
                result["probes"]["crawl4ai"] = {"ok": True, "chars": len(d.content)}
                if not as_json:
                    click.secho(f"crawl4ai: OK ({len(d.content)} chars)", fg="green")
            except ProviderError as e:
                result["probes"]["crawl4ai"] = {"ok": False, "error": str(e)[:200]}
                if not as_json:
                    click.secho(f"crawl4ai: FAIL ({e})", fg="red")
        else:
            result["probes"]["crawl4ai"] = {"ok": False, "error": "not configured"}
            if not as_json:
                click.secho("crawl4ai: not configured (set CRAWL4AI_URL)", fg="yellow")

        for pname in ("exa", "tavily", "firecrawl", "you_mcp"):
            p = oracle.registry.all.get(pname)
            if p is not None and not p.configured:
                if not as_json:
                    click.secho(f"{pname}: not configured (auto-skipped)", fg="yellow")

        if not as_json:
            click.secho("== Live balances (official APIs) ==", bold=True)
        balances = await oracle.live_balances()
        result["balances"] = balances
        if not as_json:
            if not balances:
                click.secho("(no provider exposes a balance API / none configured)", fg="yellow")
            for bname, bal in balances.items():
                click.secho(f"{bname}: {json.dumps(bal, ensure_ascii=False)}", fg="cyan")
            if "exa" not in balances:
                click.secho("exa: no public balance API — local USD spend is tracked "
                            "from each response's costDollars", fg="yellow")

    _run_cli(cmd)

    # Deployment is "ok" if at least one search path and one fetch path work.
    search_ok = result["probes"].get("searxng", {}).get("ok") or any(
        result["providers"].get(n, {}).get("configured") for n in ("exa", "tavily", "you_mcp"))
    fetch_ok = result["probes"].get("crawl4ai", {}).get("ok") or any(
        result["providers"].get(n, {}).get("configured")
        for n in ("jina_reader", "firecrawl", "light_http"))
    result["ok"] = bool(search_ok and fetch_ok)
    result["search_path_ok"] = bool(search_ok)
    result["fetch_path_ok"] = bool(fetch_ok)

    if as_json:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        if not result["ok"]:
            raise SystemExit(1)
        return
    click.secho("Doctor finished. Missing keys = provider auto-skipped, never fatal.",
                fg="cyan")
    if not result["ok"]:
        click.secho("WARNING: no usable search or fetch path detected.", fg="red")
        raise SystemExit(1)


if __name__ == "__main__":
    cli()
