"""Reproducible retrieval-quality evaluation on a fixed, public sample set.

This is deliberately not a semantic-quality benchmark: it measures whether the
current routing returns an expected official domain and expected keywords for a
small fixed set, plus latency, provider mix and empty/error rates. Real runs
consume provider free quota and require ``--live``.
"""
from __future__ import annotations

from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlparse

import yaml

DEFAULT_SAMPLES = Path(__file__).resolve().parent / "config" / "defaults" / "search_quality_samples.yaml"


def load_samples(path: str | Path | None = None) -> list[dict[str, Any]]:
    target = Path(path) if path else DEFAULT_SAMPLES
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    samples = raw.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("quality sample file must contain a non-empty samples list")
    result = []
    seen = set()
    for item in samples:
        if not isinstance(item, dict):
            raise ValueError("each sample must be an object")
        sample_id = str(item.get("id", "")).strip()
        query = str(item.get("query", "")).strip()
        if not sample_id or not query or sample_id in seen:
            raise ValueError("sample id and query are required and ids must be unique")
        seen.add(sample_id)
        domains = item.get("expected_domains") or []
        keywords = item.get("expected_keywords") or []
        if not isinstance(domains, list) or not all(isinstance(x, str) and x for x in domains):
            raise ValueError("expected_domains must be a list of strings")
        if not isinstance(keywords, list) or not all(isinstance(x, str) and x for x in keywords):
            raise ValueError("expected_keywords must be a list of strings")
        result.append({
            "id": sample_id,
            "query": query,
            "language": str(item.get("language", "")),
            "expected_domains": domains,
            "expected_keywords": keywords,
        })
    return result


def _host_matches(url: str, domain: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    domain = domain.lower().rstrip(".")
    return bool(host) and (host == domain or host.endswith("." + domain))


def _text_of(result) -> str:
    return " ".join(str(part or "") for part in (
        getattr(result, "title", ""), getattr(result, "snippet", ""),
        getattr(result, "content", ""))).casefold()


def _row(sample, *, mode, max_results, results, providers, latency_ms,
         error="", haystack="") -> dict[str, Any]:
    domain_hit = any(
        _host_matches(getattr(result, "url", ""), domain)
        for result in results for domain in sample["expected_domains"])
    if sample["expected_keywords"]:
        keyword_hit = any(word.casefold() in haystack
                          for word in sample["expected_keywords"])
    else:
        keyword_hit = None
    return {
        "id": sample["id"],
        "query": sample["query"],
        "language": sample["language"],
        "mode": mode,
        "max_results": max_results,
        "expected_domains": sample["expected_domains"],
        "expected_keywords": sample["expected_keywords"],
        "domain_hit": domain_hit,
        "keyword_hit": keyword_hit,
        "results": len(results),
        "providers": providers,
        "latency_ms": latency_ms,
        "error": error,
        "top_urls": [getattr(result, "url", "") for result in results[:3]],
    }


def _aggregate(rows: list[dict[str, Any]], *, mode: str, max_results: int,
               bypass_cache: bool, source: str) -> dict[str, Any]:
    total = len(rows)
    keyword_rows = [row for row in rows if row["keyword_hit"] is not None]
    provider_counts: dict[str, int] = {}
    for row in rows:
        for provider in row["providers"]:
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
    return {
        "generated_at": time.time(),
        "source": source,
        "mode": mode,
        "max_results": max_results,
        "bypass_cache": bypass_cache,
        "samples": total,
        "domain_hits": sum(1 for row in rows if row["domain_hit"]),
        "domain_recall": round(sum(1 for row in rows if row["domain_hit"]) / total, 4) if total else 0.0,
        "keyword_hits": sum(1 for row in keyword_rows if row["keyword_hit"]),
        "keyword_samples": len(keyword_rows),
        "keyword_recall": round(sum(1 for row in keyword_rows if row["keyword_hit"]) / len(keyword_rows), 4) if keyword_rows else None,
        "empty_results": sum(1 for row in rows if row["results"] == 0),
        "errors": sum(1 for row in rows if row["error"]),
        "average_latency_ms": round(sum(row["latency_ms"] for row in rows) / total, 1) if total else 0.0,
        "provider_counts": dict(sorted(provider_counts.items())),
        "rows": rows,
    }


async def evaluate_samples(oracle, samples: list[dict[str, Any]], *,
                           mode: str = "auto", max_results: int = 5,
                           bypass_cache: bool = True) -> dict[str, Any]:
    rows = []
    for sample in samples:
        started = time.monotonic()
        error = ""
        try:
            response = await oracle.web_search(
                sample["query"], mode=mode, max_results=max_results,
                bypass_cache=bypass_cache)
            results = list(response.results)
            providers = list(response.providers_used)
        except Exception as exc:  # preserve the evaluation row and continue
            results = []
            providers = []
            error = type(exc).__name__
        latency_ms = int((time.monotonic() - started) * 1000)
        haystack = " ".join(_text_of(result) for result in results)
        rows.append(_row(sample, mode=mode, max_results=max_results,
                         results=results, providers=providers,
                         latency_ms=latency_ms, error=error, haystack=haystack))
    return _aggregate(rows, mode=mode, max_results=max_results,
                      bypass_cache=bypass_cache, source="direct")


def parse_mcp_search_text(text: str) -> dict[str, Any]:
    """Parse the existing MCP web_search text format for read-only evaluation."""
    urls = re.findall(r"(?m)^\s+(https?://\S+)\s*$", text or "")
    provider_match = re.search(r"providers=([^\s]+)", text or "")
    providers = [item for item in (provider_match.group(1).split(",") if provider_match else []) if item]
    results_match = re.search(r"results=(\d+)", text or "")
    count = int(results_match.group(1)) if results_match else len(urls)
    return {"urls": urls, "providers": providers, "count": count,
            "is_error": (text or "").lstrip().startswith("error:")}


async def evaluate_mcp_samples(url: str, token: str, samples: list[dict[str, Any]], *,
                               mode: str = "auto", max_results: int = 5) -> dict[str, Any]:
    """Evaluate a deployed MCP instance without importing its source tree."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    rows = []
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for sample in samples:
                started = time.monotonic()
                error = ""
                parsed = {"urls": [], "providers": [], "count": 0, "is_error": False}
                text = ""
                try:
                    result = await session.call_tool("web_search", {
                        "query": sample["query"], "mode": mode,
                        "max_results": max_results})
                    text = "".join(
                        block.text for block in result.content
                        if getattr(block, "type", "") == "text")
                    parsed = parse_mcp_search_text(text)
                    if getattr(result, "isError", False) or parsed["is_error"]:
                        error = "mcp_error"
                except Exception as exc:
                    error = type(exc).__name__
                latency_ms = int((time.monotonic() - started) * 1000)

                class _Result:
                    def __init__(self, url):
                        self.url = url

                results = [_Result(url) for url in parsed["urls"]]
                row = _row(sample, mode=mode, max_results=max_results,
                           results=results, providers=parsed["providers"],
                           latency_ms=latency_ms, error=error,
                           haystack=(text or "").casefold())
                row["results"] = parsed["count"]
                rows.append(row)
    return _aggregate(rows, mode=mode, max_results=max_results,
                      bypass_cache=False, source="mcp")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 固定样本检索质量评估",
        "",
        f"- 生成时间（UTC）：{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(report['generated_at']))}",
        f"- 数据来源：`{report.get('source', 'direct')}`；绕过缓存：{report.get('bypass_cache')}",
        f"- 模式：`{report['mode']}`，每样本最多 {report['max_results']} 条",
        f"- 样本数：{report['samples']}；领域命中：{report['domain_hits']}/{report['samples']} "
        f"（{report['domain_recall']:.0%}）",
        f"- 关键词命中：{report['keyword_hits']}/{report['keyword_samples']}"
        if report["keyword_samples"] else "- 关键词命中：未配置",
        f"- 空结果：{report['empty_results']}；异常：{report['errors']}；平均耗时：{report['average_latency_ms']} ms",
        f"- 供应商出现次数：{', '.join(f'{k}={v}' for k, v in report['provider_counts'].items()) or '无'}",
        "",
        "> 这是固定样本的领域/关键词命中与运行指标，不是语义相关性、事实准确性或全网召回率评估。",
        "",
        "| 样本 | 领域命中 | 关键词命中 | 结果数 | 供应商 | 耗时 ms |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        keyword = "—" if row["keyword_hit"] is None else ("是" if row["keyword_hit"] else "否")
        lines.append(
            f"| {row['id']} | {'是' if row['domain_hit'] else '否'} | {keyword} | "
            f"{row['results']} | {', '.join(row['providers']) or '—'} | {row['latency_ms']} |")
    lines.append("")
    lines.append("## 样本与预期")
    lines.append("")
    for row in report["rows"]:
        lines.append(
            f"- `{row['id']}`：{row['query']}；预期域名 {', '.join(row['expected_domains']) or '—'}；"
            f"预期关键词 {', '.join(row['expected_keywords']) or '—'}；"
            f"实际前三 URL {', '.join(row['top_urls']) or '—'}")
    return "\n".join(lines) + "\n"
