FROM python:3.12-slim

LABEL org.opencontainers.image.title="MagicalGirlWebOracle" \
      org.opencontainers.image.description="Unified free-tier web search, research and fetch routing for AI agents over MCP." \
      org.opencontainers.image.source="https://github.com/LingMoe404/MagicalGirlWebOracle" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Config YAMLs ship inside the package (config/defaults), so no separate COPY.
COPY pyproject.toml README.md ./
COPY src ./src
COPY skills ./skills

RUN pip install --no-cache-dir .

# Quota ledger lives on a volume so it survives restarts and is shared by
# every machine that connects to this instance.
VOLUME ["/app/data"]

# Default: stdio (single-machine). For a shared deployment set
#   MCP_TRANSPORT=streamable-http  MCP_PORT=28288  MCP_AUTH_TOKEN=<secret>
# MCP_HOST is 0.0.0.0 inside the container; publish the port on a specific
# host interface with docker compose (MCP_BIND_IP).
ENV MCP_TRANSPORT=stdio \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=28288 \
    MCP_PATH=/mcp \
    USAGE_DB_PATH=/app/data/usage.sqlite3

EXPOSE 28288

# Self-check without needing a shell in the image. In stdio mode there is no
# HTTP port, so the probe only asserts the package imports cleanly.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,sys,urllib.request; \
t=os.getenv('MCP_TRANSPORT','stdio'); \
p=os.getenv('MCP_PORT','28288'); \
sys.exit(0 if t=='stdio' or urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz',timeout=3).status==200 else 1)"

ENTRYPOINT ["python", "-m", "magicalgirl_web_oracle.mcp_server.server"]
