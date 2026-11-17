"""Offline tests for the fixed-sample quality evaluator."""
from __future__ import annotations

import asyncio

import pytest

from magicalgirl_web_oracle.models.schemas import SearchResponse, SearchResult
from magicalgirl_web_oracle.quality_eval import (
    evaluate_samples,
    load_samples,
    render_markdown,
)


class FakeOracle:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def web_search(self, query, *, mode="auto", max_results=5, bypass_cache=False):
        self.calls.append((query, mode, max_results, bypass_cache))
        return self.responses[query]


def sample(query, domains=None, keywords=None):
    return {"id": query.replace(" ", "-"), "query": query, "language": "en",
            "expected_domains": domains or [], "expected_keywords": keywords or []}


def response(query, urls, snippet="text"):
    return SearchResponse(
        query=query, mode="auto", providers_used=["searxng"],
        results=[SearchResult(title="title", url=url, snippet=snippet,
                              provider="searxng") for url in urls])


def test_load_samples_rejects_duplicates(tmp_path):
    path = tmp_path / "samples.yaml"
    path.write_text("samples:\n  - id: a\n    query: q\n  - id: a\n    query: r\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_samples(path)


def test_evaluate_domain_and_keyword_hits():
    queries = {
        "docs query": response("docs query", ["https://docs.python.org/3/library/asyncio.html"],
                               "asyncio timeout documentation"),
        "wrong query": response("wrong query", ["https://example.com/page"], "unrelated"),
    }
    oracle = FakeOracle(queries)
    samples = [
        sample("docs query", ["python.org"], ["asyncio"]),
        sample("wrong query", ["python.org"], ["asyncio"]),
    ]
    report = asyncio.run(evaluate_samples(oracle, samples, mode="fast", max_results=3))
    assert report["samples"] == 2
    assert report["domain_hits"] == 1
    assert report["keyword_hits"] == 1
    assert report["domain_recall"] == 0.5
    assert report["keyword_recall"] == 0.5
    assert report["rows"][0]["top_urls"][0].startswith("https://docs.python.org")
    assert oracle.calls[0][3] is True  # bypass_cache


def test_evaluate_records_errors_without_stopping():
    class FailingOracle:
        async def web_search(self, query, **kwargs):
            if query == "bad":
                raise RuntimeError("boom")
            return response(query, ["https://example.com"])

    report = asyncio.run(evaluate_samples(
        FailingOracle(), [sample("bad"), sample("good")], mode="fast"))
    assert report["errors"] == 1
    assert report["rows"][0]["error"] == "RuntimeError"
    assert report["rows"][1]["results"] == 1


def test_render_markdown_has_metrics_and_no_credentials():
    report = asyncio.run(evaluate_samples(
        FakeOracle({"q": response("q", ["https://docs.python.org/x"], "keyword")}),
        [sample("q", ["python.org"], ["keyword"])], mode="auto"))
    text = render_markdown(report)
    assert "固定样本检索质量评估" in text
    assert "100%" in text
    assert "不是语义相关性" in text
    assert "tvly-" not in text and "jina_" not in text


def test_bundled_samples_are_valid():
    samples = load_samples()
    assert len(samples) >= 6
    assert all(item["query"] and item["expected_domains"] for item in samples)


def test_parse_mcp_search_text():
    from magicalgirl_web_oracle.quality_eval import parse_mcp_search_text
    text = ("mode=auto providers=searxng,exa results=2 dedup 2->2\n"
            "1. Python docs\n   https://docs.python.org/3/library/asyncio.html\n"
            "   asyncio timeout docs\n   by:searxng\n"
            "2. Other\n   https://example.com/page\n   other\n   by:exa\n"
            "note: something")
    parsed = parse_mcp_search_text(text)
    assert parsed["providers"] == ["searxng", "exa"]
    assert parsed["count"] == 2
    assert parsed["urls"] == ["https://docs.python.org/3/library/asyncio.html",
                              "https://example.com/page"]
    assert parsed["is_error"] is False
    assert parse_mcp_search_text("error: quota: no budget")["is_error"] is True
