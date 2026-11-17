#!/usr/bin/env bash
# MagicalGirlWebOracle — one-command deploy for the NAS (or any Docker host).
#
# Usage:
#   bash deploy.sh                    # generic deploy (auto-detect, random token)
#   bash deploy.sh --profile <name>   # apply deploy/<name>/profile.conf first
#   bash deploy.sh --help
#
# Some filesystems (e.g. fnOS clones with core.filemode=false) do not carry the
# exec bit, so `./deploy.sh` can fail with "Permission denied"; invoking via
# `bash` always works.
#
# Profile mechanism (files under deploy/<name>/):
#   profile.conf  KEY=VALUE lines. Each key is written into .env only when the
#                 key is missing or empty — existing values are kept. Two meta
#                 keys are NOT written into .env:
#                   AUTH_MODE  on|off (default on). "off" leaves MCP_AUTH_TOKEN
#                            empty on first create; to enable auth later, edit
#                            .env directly and run: docker compose up -d
#                   WG_HINT    free text shown in the final output, for hosts
#                            where WireGuard runs elsewhere (e.g. a router)
#   post_env.sh   optional hook, run as `bash post_env.sh .env` after .env is
#                 assembled (e.g. to sync tokens from sibling stacks)
#
# What it does:
#   1. Assembles .env (base: .env.example; profile fills blanks; random token
#      only on first create and only when AUTH_MODE=on)
#   2. Builds the image
#   3. Auto-detects SEARXNG_URL / CRAWL4AI_URL from inside a container unless
#      already set (profiles normally pin these)
#   4. Starts the service and verifies with `magicalgirl-web doctor --json`
#   5. Prints the MCP endpoint and a ready-to-paste client config (headers are
#      included only when auth is enabled)
#
# Idempotent: safe to re-run. Secrets are never committed.
# Note: values written via this script must not contain | or & (sed-safe rule).

set -euo pipefail

cd "$(dirname "$0")"

log()  { printf '\033[36m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'U'
Usage:
  bash deploy.sh                    generic deploy (auto-detect, random token)
  bash deploy.sh --profile <name>   apply deploy/<name>/profile.conf first
  bash deploy.sh --help             this help

Some filesystems (e.g. fnOS clones) do not carry the exec bit, so `./deploy.sh`
can fail with "Permission denied"; invoking via `bash` always works.
U
}

# ---------------------------------------------------------------------------
# 0. Arguments
# ---------------------------------------------------------------------------
PROFILE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --profile)    [ $# -ge 2 ] || die "--profile requires a name"; PROFILE="$2"; shift 2 ;;
    --profile=*)  PROFILE="${1#*=}"; shift ;;
    -h|--help)    usage; exit 0 ;;
    *)            die "unknown argument: $1 (try --help)" ;;
  esac
done

PROFILE_DIR=""
WG_HINT=""
AUTH_MODE="on"
if [ -n "$PROFILE" ]; then
  PROFILE_DIR="deploy/$PROFILE"
  [ -f "$PROFILE_DIR/profile.conf" ] || die "profile not found: $PROFILE_DIR/profile.conf"
  profile_get() { sed -n "s|^$1=||p" "$PROFILE_DIR/profile.conf" | tail -1; }
  WG_HINT="$(profile_get WG_HINT)"
  pm="$(profile_get AUTH_MODE)"
  [ -n "$pm" ] && AUTH_MODE="$pm"
  case "$AUTH_MODE" in on|off) ;; *) die "AUTH_MODE must be 'on' or 'off', got: $AUTH_MODE" ;; esac
  log "using profile '$PROFILE' (AUTH_MODE=$AUTH_MODE)"
fi

# ---------------------------------------------------------------------------
# 1. Prerequisites
# ---------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "docker not found. Install Docker first."
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  die "docker compose plugin not found."
fi

IMAGE="magicalgirl-web-oracle:0.1.0"

# ---------------------------------------------------------------------------
# 2. .env assembly — existing non-empty values always win; blanks get filled
# ---------------------------------------------------------------------------
get_env() { grep -E "^$1=" .env 2>/dev/null | cut -d= -f2- | tr -d ' \r' || true; }

set_if_empty() { # set_if_empty KEY VALUE
  local key="$1" val="$2"
  grep -q "^$key=." .env && return 0            # already set -> keep
  if grep -q "^$key=" .env; then
    sed -i.bak "s|^$key=.*|$key=$val|" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "$key" "$val" >> .env
  fi
}

CREATED=0
if [ ! -f .env ]; then
  log "creating .env from .env.example"
  cp .env.example .env
  sed -i.bak 's|^MCP_TRANSPORT=.*|MCP_TRANSPORT=streamable-http|' .env && rm -f .env.bak
  CREATED=1
else
  log ".env already exists — existing values kept, blanks filled"
fi

if [ -n "$PROFILE_DIR" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
      *=*)     ;;
      *) warn "profile.conf: ignoring line without '=': $line"; continue ;;
    esac
    key="${line%%=*}"; val="${line#*=}"
    case "$key" in AUTH_MODE|WG_HINT) continue ;; esac
    set_if_empty "$key" "$val"
  done < "$PROFILE_DIR/profile.conf"
  log "profile values applied to .env (blanks only)"
fi

if [ "$CREATED" = "1" ] && [ "$AUTH_MODE" = "on" ] && [ -z "$(get_env MCP_AUTH_TOKEN)" ]; then
  TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null \
           || openssl rand -base64 32 | tr -d '/+=' | cut -c1-43)"
  set_if_empty MCP_AUTH_TOKEN "$TOKEN"
  log "generated MCP_AUTH_TOKEN (auth ON)"
elif [ "$AUTH_MODE" = "off" ]; then
  if [ -z "$(get_env MCP_AUTH_TOKEN)" ]; then
    log "auth disabled (AUTH_MODE=off) — MCP_AUTH_TOKEN stays empty"
  else
    log "auth disabled (AUTH_MODE=off), but an existing MCP_AUTH_TOKEN is kept (auth ON)"
  fi
fi

if [ -n "$PROFILE_DIR" ] && [ -f "$PROFILE_DIR/post_env.sh" ]; then
  if bash "$PROFILE_DIR/post_env.sh" .env; then
    log "post_env hook finished"
  else
    warn "post_env.sh exited non-zero (continuing)"
  fi
fi

# ---------------------------------------------------------------------------
# 3. Build first — we need the image to probe from the container's viewpoint
# ---------------------------------------------------------------------------
log "building image (first build downloads Python deps, ~1-2 min)"
$DC build

probe_in_container() { # probe_in_container <url> -> 0 if reachable
  docker run --rm --add-host=host.docker.internal:host-gateway \
    "$IMAGE" python - "$1" <<'PY' >/dev/null 2>&1
import sys, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=4)
except Exception:
    sys.exit(1)
PY
}

detect() { # detect <ENV_KEY> <path> <candidate...>
  local key="$1"; shift
  local path="$1"; shift
  if [ -n "$(get_env "$key")" ]; then
    log "$key already set — skipping detection"
    return 0
  fi
  local cand
  for cand in "$@"; do
    if probe_in_container "${cand}${path}"; then
      set_if_empty "$key" "$cand"
      log "detected $key at ${cand} (reachable from container)"
      return 0
    fi
  done
  warn "$key not auto-detected — set it manually in .env"
  return 0
}

# Candidates cover: same docker network by name, host via host-gateway,
# docker bridge gateway, and localhost (when running with --network host).
detect SEARXNG_URL "/search?q=test&format=json" \
  "http://searxng:8080" "http://searxng:8888" \
  "http://host.docker.internal:8888" "http://host.docker.internal:8080" \
  "http://172.17.0.1:8888" "http://127.0.0.1:8888"

detect CRAWL4AI_URL "/health" \
  "http://crawl4ai:11235" "http://host.docker.internal:11235" \
  "http://172.17.0.1:11235" "http://127.0.0.1:11235"

# ---------------------------------------------------------------------------
# 4. Start
# ---------------------------------------------------------------------------
log "starting container"
$DC up -d

PORT="$(get_env MCP_PORT)"; PORT="${PORT:-28288}"
BIND_IP="$(get_env MCP_BIND_IP)"
HEALTH_HOST="127.0.0.1"
if [ -n "$BIND_IP" ] && [ "$BIND_IP" != "0.0.0.0" ]; then
  HEALTH_HOST="$BIND_IP"
fi

log "waiting for http://${HEALTH_HOST}:${PORT}/healthz"
for i in $(seq 1 30); do
  if curl -fsS --noproxy '*' --max-time 4 "http://${HEALTH_HOST}:${PORT}/healthz" >/dev/null 2>&1; then
    log "service is up"
    break
  fi
  [ "$i" -eq 30 ] && die "service did not become healthy in 60s. Check: $DC logs"
  sleep 2
done

# ---------------------------------------------------------------------------
# 5. Authoritative diagnostics from inside the container
# ---------------------------------------------------------------------------
log "running in-container diagnostics"
DOCTOR_OUT="$($DC exec -T oracle magicalgirl-web doctor --json 2>&1 || true)"
printf '%s\n' "$DOCTOR_OUT"

if ! printf '%s' "$DOCTOR_OUT" | grep -q '"ok": true'; then
  warn "doctor reported problems. Common fixes:"
  warn "  - SearXNG must allow JSON: settings.yml -> search.formats: [html, json]"
  warn "  - If SearXNG/Crawl4AI run as containers, put Oracle on the same docker"
  warn "    network and use the service name (e.g. http://searxng:8080)"
  warn "  - Or point SEARXNG_URL at http://host.docker.internal:<port>"
fi

# ---------------------------------------------------------------------------
# 6. Client connection details
# ---------------------------------------------------------------------------
LANIP="$(hostname -I 2>/dev/null | awk '{print $1}')"
# WireGuard interfaces conventionally start with "wg" (wg0, wgs1, ...).
# Do not guess from private ranges — docker bridges would match too.
WGIP="$(ip -o -4 addr show 2>/dev/null | awk '$2 ~ /^wg/ {print $4}' | cut -d/ -f1 | head -1)"
LANIP="${LANIP:-<nas-ip>}"
TOK="$(get_env MCP_AUTH_TOKEN)"

WG_LINE=""
if [ -n "$WGIP" ]; then
  WG_LINE="   WireGuard  : http://${WGIP}:${PORT}/mcp (tunnel on this host)"
fi
HINT_LINE=""
if [ -n "$WG_HINT" ]; then
  HINT_LINE="   Note       : $WG_HINT"
fi

if [ -n "$TOK" ]; then
  AUTH_STATE="Authorization: Bearer ${TOK}"
  CLIENT_JSON="{
   \"mcpServers\": {
     \"magicalgirl-web-oracle\": {
       \"type\": \"streamable-http\",
       \"url\": \"http://<reachable-ip>:${PORT}/mcp\",
       \"headers\": { \"Authorization\": \"Bearer ${TOK}\" }
     }
   }
 }"
else
  AUTH_STATE="none — LAN/tunnel trust only (set MCP_AUTH_TOKEN in .env, then '$DC up -d' to enable)"
  CLIENT_JSON="{
   \"mcpServers\": {
     \"magicalgirl-web-oracle\": {
       \"type\": \"streamable-http\",
       \"url\": \"http://<reachable-ip>:${PORT}/mcp\"
     }
   }
 }"
fi

cat <<EOF

================================================================
 MagicalGirlWebOracle is running.

 Reachable at (bound to ${BIND_IP:-0.0.0.0 = all interfaces}):
   LAN        : http://${LANIP}:${PORT}/mcp
${WG_LINE}
${HINT_LINE}
   Health     : http://${LANIP}:${PORT}/healthz
 Auth         : ${AUTH_STATE}

 On every machine that should use it, add to the MCP client config
 (pick the address that machine can reach):

 ${CLIENT_JSON}

 Logs : $DC logs -f
 Stop : $DC down
================================================================
EOF
