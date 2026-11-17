"""URL canonicalization & multi-provider result merging.

Cleans utm/tracking params, fragments, duplicate slashes, www-prefixes and
common promo params. Results found by multiple providers are MERGED (found_by
list grows) — agreement across independent sources is a credibility signal.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

# Tracking / promo params commonly stripped.
_STRIP_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_cid", "utm_reader", "utm_social", "utm_viz_id",
    "utm", "gclid", "gclsrc", "gbraid", "wbraid", "dclid", "fbclid", "msclkid",
    "mc_cid", "mc_eid", "igshid", "igsh", "mkt_tok", "vero_id", "wickedid",
    "yclid", "_ga", "_gl", "spm", "scm", "share_token", "share_medium",
    "share_plat", "share_source", "share_tag", "share_user_id", "ref",
    "ref_src", "ref_url", "referral", "referral_code", " affiliate",
    "from", "tt_from", "f", "fr", "s_from", "ns", "ts", "vd", "vt",
}

# Params kept only if they change content meaningfully for common sites.
_YT_KEEP = {"v"}  # youtube watch?v=
_BILI_KEEP = {"bvid", "p"}

_TRACKING_PATH = re.compile(r"/(?:utm_[a-z]+|ref)/")


def canonicalize_url(url: str) -> str:
    """Normalize a URL for dedup identity. Content keys (video ids) survive."""
    url = (url or "").strip()
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    scheme = parts.scheme.lower() or "https"
    host = parts.netloc.lower()

    # Strip default ports.
    if host.endswith(":80") and scheme == "http":
        host = host[:-3]
    if host.endswith(":443") and scheme == "https":
        host = host[:-4]

    # www handling: drop for most hosts (bilibili.com == www.bilibili.com).
    if host.startswith("www."):
        host = host[4:]

    # Drop tracking-y path segments (e.g. /utm_source/x).
    path = _TRACKING_PATH.sub("/", parts.path or "/")
    # Collapse duplicate slashes, remove trailing slash (keep root).
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # Query cleanup.
    q = []
    if host.endswith("youtube.com") and path == "/watch":
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k in _YT_KEEP]
    elif host.endswith("bilibili.com"):
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k in _BILI_KEEP]
    else:
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in _STRIP_PARAMS and k.lower().strip() not in _STRIP_PARAMS]

    query = urlencode(q)

    # Spaces etc. stay encoded.
    path = quote(path, safe="/%:@!$&'()*+,;=~")
    return urlunsplit((scheme, host, path, query, ""))  # fragment dropped


def merge_results(results: list) -> list:
    """Dedup by canonical URL, merging found_by/metadata. Preserves best fields."""
    from ..models.schemas import SearchResult

    merged: dict[str, SearchResult] = {}
    for r in results:
        key = canonicalize_url(r.url)
        if not key:
            continue
        if key in merged:
            base = merged[key]
            providers = set(base.found_by) | set(r.found_by) | {r.provider}
            base.found_by = sorted(providers)
            # Keep the richer content/snippet/title.
            if len(r.content or "") > len(base.content or ""):
                base.content = r.content
            if len(r.snippet or "") > len(base.snippet or ""):
                base.snippet = r.snippet
            if (not base.title) and r.title:
                base.title = r.title
            if not base.published_at and r.published_at:
                base.published_at = r.published_at
            base.score = max(base.score, r.score)
            base.metadata.setdefault("also_by", []).append(r.provider)
        else:
            r = r.model_copy(deep=True)
            r.url = key
            if r.provider not in r.found_by:
                r.found_by = sorted(set(r.found_by) | {r.provider})
            merged[key] = r
    return list(merged.values())


def agreement_score(results: list) -> dict[str, float]:
    """For each canonical URL: fraction = number_of_independent_sources / 3 cap."""
    out = {}
    for r in results:
        n = len(set(r.found_by))
        out[canonicalize_url(r.url)] = min(1.0, n / 3.0)
    return out
