"""Central settings loading from .env + config YAML files."""

from __future__ import annotations

import os
import math
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Config lookup order:
#   1. PROJECT_ROOT/config            (running from a source checkout)
#   2. <project root>/config relative to CWD (docker WORKDIR=/app)
#   3. package-bundled defaults        (pip-installed wheel / site-packages)
# The bundled copy guarantees the service works when the package is installed
# outside a source tree, which is exactly what happens inside the Docker image.
_PKG_DEFAULTS = Path(__file__).resolve().parent / "defaults"
_CFG_CANDIDATES = [
    PROJECT_ROOT / "config",
    Path.cwd() / "config",
    _PKG_DEFAULTS,
]


def _load_env() -> None:
    # Do NOT override variables the user already exported.
    for env_path in (PROJECT_ROOT / ".env", Path.cwd() / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)
            break


_load_env()


def _config_dir() -> Path:
    for c in _CFG_CANDIDATES:
        if (c / "providers.yaml").exists():
            return c
    raise FileNotFoundError(
        "providers.yaml not found in: " + ", ".join(str(c) for c in _CFG_CANDIDATES))


def load_yaml(name: str) -> dict:
    path = _config_dir() / name
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    """Immutable-ish runtime settings. API keys never logged, never serialized."""

    def __init__(self) -> None:
        self.project_root = PROJECT_ROOT
        self.exa_api_key: str = os.getenv("EXA_API_KEY", "").strip()
        self.tavily_api_key: str = os.getenv("TAVILY_API_KEY", "").strip()
        self.firecrawl_api_key: str = os.getenv("FIRECRAWL_API_KEY", "").strip()
        self.jina_api_key: str = os.getenv("JINA_API_KEY", "").strip()
        self.ydc_api_key: str = os.getenv("YDC_API_KEY", "").strip()

        self.searxng_url: str = os.getenv("SEARXNG_URL", "").strip().rstrip("/")
        self.crawl4ai_url: str = os.getenv("CRAWL4AI_URL", "").strip().rstrip("/")
        self.crawl4ai_token: str = os.getenv("CRAWL4AI_TOKEN", "").strip()

        # Outbound proxy for external providers (Exa/Tavily/Firecrawl/Jina/You).
        # Self-hosted LAN services (SearXNG/Crawl4AI) always bypass it.
        self.proxy_url: str = (
            os.getenv("PROXY_URL", "").strip()
            or os.getenv("HTTPS_PROXY", "").strip()
            or os.getenv("HTTP_PROXY", "").strip()
        )

        # HARD RULE: paid overage off by default; nothing turns it on automatically.
        self.allow_paid_overage: bool = _env_bool("ALLOW_PAID_OVERAGE", False)

        self.default_mode: str = os.getenv("SEARCH_MODE_DEFAULT", "auto").strip().lower()
        # Resolve relative DB paths against the working directory (docker: /app).
        db_env = os.getenv("USAGE_DB_PATH", "").strip()
        if db_env:
            self.usage_db_path = db_env
        else:
            root = PROJECT_ROOT if (PROJECT_ROOT / "config").exists() else Path.cwd()
            self.usage_db_path = str(root / "data" / "usage.sqlite3")
        self.store_query_text: bool = _env_bool("STORE_QUERY_TEXT", False)
        self.query_hash_salt: str = os.getenv("QUERY_HASH_SALT", "magicalgirl-default-salt")
        # Opt-in. Leaving this empty preserves the existing single-key setup.
        self.accounts_file: str = os.getenv("ORACLE_ACCOUNTS_FILE", "").strip()
        self.admin_auth_token: str = os.getenv("ORACLE_ADMIN_TOKEN", "").strip()
        self.vault_key: str = os.getenv("ORACLE_VAULT_KEY", "").strip()
        self.balance_sync_interval: int = int(os.getenv("ORACLE_BALANCE_SYNC_INTERVAL", "300") or 300)
        if self.balance_sync_interval!=0 and not 60<=self.balance_sync_interval<=480:
            raise ValueError("ORACLE_BALANCE_SYNC_INTERVAL must be 0 or between 60 and 480 seconds")
        self.low_quota_ratio: float = float(os.getenv("ORACLE_LOW_QUOTA_RATIO", "0.1") or 0.1)
        if not math.isfinite(self.low_quota_ratio) or not 0<=self.low_quota_ratio<=1:
            raise ValueError("ORACLE_LOW_QUOTA_RATIO must be a finite fraction from 0 to 1")
        self.jina_keyless_fallback: bool = _env_bool("JINA_KEYLESS_FALLBACK", True)

        # Stage 4 cache is opt-in: 0 keeps the pre-cache behavior. When search
        # cache is enabled, fetch defaults to a longer TTL unless overridden.
        self.cache_ttl_seconds: int = int(os.getenv("ORACLE_CACHE_TTL_SECONDS", "0") or 0)
        if not 0 <= self.cache_ttl_seconds <= 86400:
            raise ValueError("ORACLE_CACHE_TTL_SECONDS must be 0 or between 1 and 86400")
        fetch_default = "3600" if self.cache_ttl_seconds > 0 else "0"
        self.fetch_cache_ttl_seconds: int = int(
            os.getenv("ORACLE_FETCH_CACHE_TTL_SECONDS", fetch_default) or fetch_default)
        if not 0 <= self.fetch_cache_ttl_seconds <= 86400:
            raise ValueError("ORACLE_FETCH_CACHE_TTL_SECONDS must be 0 or between 1 and 86400")
        self.cache_max_entries: int = int(os.getenv("ORACLE_CACHE_MAX_ENTRIES", "500") or 500)
        if not 10 <= self.cache_max_entries <= 10000:
            raise ValueError("ORACLE_CACHE_MAX_ENTRIES must be between 10 and 10000")
        self.cache_path: str = os.getenv("ORACLE_CACHE_PATH", "").strip() or str(
            Path(self.usage_db_path).with_name("cache.sqlite3"))
        self.scheduling_strategy: str = os.getenv(
            "ORACLE_SCHEDULING_STRATEGY", "priority").strip().lower() or "priority"
        if self.scheduling_strategy not in ("priority", "balanced", "expiry", "preferred_account"):
            raise ValueError("ORACLE_SCHEDULING_STRATEGY must be priority, balanced, expiry, or preferred_account")
        self.preferred_account: str = os.getenv("ORACLE_PREFERRED_ACCOUNT", "").strip()
        if self.scheduling_strategy == "preferred_account" and not self.preferred_account:
            raise ValueError("ORACLE_PREFERRED_ACCOUNT is required for preferred_account scheduling")

        # MCP transport: stdio (default, single machine) | streamable-http | sse.
        # HTTP mode is for deploying one shared Oracle on a trusted host so
        # several machines share ONE quota ledger (otherwise each machine
        # under-counts). Bind loopback unless you explicitly opt into LAN access.
        self.mcp_transport: str = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
        self.mcp_host: str = os.getenv("MCP_HOST", "127.0.0.1").strip()
        self.mcp_port: int = int(os.getenv("MCP_PORT", "28288") or 28288)
        self.mcp_path: str = os.getenv("MCP_PATH", "/mcp").strip()
        # Which host interface publishes the port (used by docker-compose).
        # 127.0.0.1 = local only; 0.0.0.0 = all interfaces; or one specific IP.
        self.mcp_bind_ip: str = os.getenv("MCP_BIND_IP", "127.0.0.1").strip()
        # Optional shared secret. Empty => no auth (trust your network only).
        self.mcp_auth_token: str = os.getenv("MCP_AUTH_TOKEN", "").strip()

        self.providers_cfg: dict = load_yaml("providers.yaml")
        self.router_policy: dict = load_yaml("router_policy.yaml")

    def masked_summary(self) -> dict:
        """Safe-to-print settings summary (for `status` / `doctor`)."""

        def mask(k: str) -> str:
            if not k:
                return "(not set)"
            return f"{k[:4]}…{k[-2:]}" if len(k) > 8 else "(set)"

        return {
            "exa_api_key": mask(self.exa_api_key),
            "tavily_api_key": mask(self.tavily_api_key),
            "firecrawl_api_key": mask(self.firecrawl_api_key),
            "jina_api_key": mask(self.jina_api_key),
            "ydc_api_key": mask(self.ydc_api_key),
            "searxng_url": self.searxng_url or "(not set)",
            "crawl4ai_url": self.crawl4ai_url or "(not set)",
            "proxy_url": self.proxy_url or "(direct)",
            "allow_paid_overage": self.allow_paid_overage,
            "default_mode": self.default_mode,
            "usage_db_path": self.usage_db_path,
            "store_query_text": self.store_query_text,
            "mcp_transport": self.mcp_transport,
            "mcp_bind": f"{self.mcp_host}:{self.mcp_port}{self.mcp_path}",
            "mcp_bind_ip": self.mcp_bind_ip,
            "mcp_auth": "enabled" if self.mcp_auth_token else "disabled",
            "key_management": "enabled" if self.vault_key else "disabled",
            "balance_sync_interval": self.balance_sync_interval,
            "low_quota_ratio": self.low_quota_ratio,
            "cache_enabled": self.cache_ttl_seconds > 0 or self.fetch_cache_ttl_seconds > 0,
            "cache_ttl_seconds": self.cache_ttl_seconds,
            "fetch_cache_ttl_seconds": self.fetch_cache_ttl_seconds,
            "scheduling_strategy": self.scheduling_strategy,
            "preferred_account": self.preferred_account or None,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
