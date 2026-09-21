#!/usr/bin/env bash
# Shared helpers for the Apple Container (container CLI) equivalents of
# docker-compose.yml. Source this file from the other scripts in this dir.
#
# Requirements:
#   - macOS 26 (Tahoe) or later, Apple silicon
#   - `container` CLI installed (github.com/apple/container), service started:
#         container system start
#
# This stack maps 1:1 to docker-compose.yml, see README.md in this folder.

set -euo pipefail

# ── Names (mirror docker-compose.yml container_name / service names) ────────
readonly NETWORK="darkweb-net"
readonly TOR_CONTAINER="darkweb-tor"
readonly API_CONTAINER="darkweb-api"
readonly CRAWLER_CONTAINER="darkweb-crawler"
readonly LOCUST_CONTAINER="darkweb-locust"

# ── Images ──────────────────────────────────────────────────────────────────
readonly TOR_IMAGE="dockurr/tor:latest"
readonly API_IMAGE="darkweb-api:latest"
readonly CRAWLER_IMAGE="darkweb-crawler:latest"
readonly LOCUST_IMAGE="locustio/locust:latest"

# ── Ports (mirror docker-compose.yml) ───────────────────────────────────────
readonly TOR_SOCKS_PORT="9050"       # SOCKS5 proxy, published to the host
readonly API_PORT="8337"             # host port for the API (8000 in container)
readonly LOCUST_PORT="8089"          # host port for the Locust UI

# ── Paths (relative to the repository root) ─────────────────────────────────
# We resolve them against the repo root so the scripts work from any CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
readonly SCRIPT_DIR REPO_ROOT

# ── Colored output (falls back to plain when not a TTY) ─────────────────────
if [[ -t 1 ]]; then
  readonly C_GREEN=$'\033[32m'
  readonly C_YELLOW=$'\033[33m'
  readonly C_RED=$'\033[31m'
  readonly C_RESET=$'\033[0m'
else
  readonly C_GREEN=""
  readonly C_YELLOW=""
  readonly C_RED=""
  readonly C_RESET=""
fi

log()  { printf '%s[container]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s[container]%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
die()  { printf '%s[container]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# ── Helpers ─────────────────────────────────────────────────────────────────

# Fail fast with clear guidance when the `container` CLI is unavailable.
require_container() {
  if ! command -v container >/dev/null 2>&1; then
    die "The 'container' CLI was not found.
  Install it from https://github.com/apple/container/releases (macOS 26+,
  Apple silicon), then run 'container system start'."
  fi
}

# Does a container with this name exist? `container run --name` uses the name
# as the container ID, so `container list -q` outputs the names.
container_exists() {
  container list -a -q 2>/dev/null | grep -qx "$1"
}

# IPv4 of a container on its first network. Apple's vmnet network does NOT
# resolve container names (v1.2.x), so peers must be addressed by IP — this
# is how the api/crawler containers reach tor (see TOR_PROXY below).
container_ip() {
  container inspect "$1" 2>/dev/null | python3 -c '
import sys, json
from pathlib import Path
path = Path(sys.argv[1])
try:
    d = json.load(sys.stdin)
    print(d[0]["status"]["networks"][0]["ipv4Address"].split("/")[0])
except Exception:
    sys.exit(1)
' "$1"
}

# Is a bind mount live inside a (running) container? Probes for a path that
# exists on the host but NOT in the image (per .dockerignore — .git is never
# baked in), so seeing it inside the container proves the mount is attached.
# CLI v1.2.x can silently drop all directory mounts when a file-source -v is
# present; this catches containers that ended up running on the image's baked
# filesystem.
container_mount_live() {
  container exec "$1" test -e "$2" 2>/dev/null
}

# Value of an env var baked into a container at creation time (empty if absent).
container_env() {
  container inspect "$1" 2>/dev/null | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    env = d[0]["configuration"]["initProcess"]["environment"]
    for e in env:
        if e.startswith(sys.argv[1] + "="):
            print(e.split("=", 1)[1])
            break
except Exception:
    sys.exit(0)
' "$1"
}

# `container run -d` on CLI v1.2.x can spuriously fail with "container already
# exists" right after creating the container. Treat that as success when the
# container actually exists afterwards; anything else propagates the failure.
run_detached() {
  local name="$1"; shift
  if ! container run -d --name "$name" "$@"; then
    if container_exists "$name"; then
      warn "'container run' reported an error but container '$name' exists — continuing (known CLI quirk)"
    else
      return 1
    fi
  fi
}

# Create the shared network if it does not exist yet.
ensure_network() {
  if container network list -q 2>/dev/null | grep -qx "$NETWORK"; then
    return 0
  fi
  log "Creating network '$NETWORK'"
  container network create "$NETWORK"
}

# Start Tor with the exact entrypoint from docker-compose.yml. The control
# password is hashed at startup so Tor never runs with
# --CookieAuthentication 0 (any container on the network could otherwise
# issue SETCONF). The single-quoted string below is evaluated by the
# container's /bin/sh at startup — ${TOR_CONTROL_PASSWORD} and $(...) are
# resolved inside the container, not by this script.
ensure_tor() {
  if container_exists "$TOR_CONTAINER"; then
    log "Container '$TOR_CONTAINER' already exists — starting it if stopped"
    container start "$TOR_CONTAINER" 2>/dev/null || true
    return 0
  fi
  log "Starting Tor container '$TOR_CONTAINER'"
  run_detached "$TOR_CONTAINER" \
    --network "$NETWORK" \
    -p "$TOR_SOCKS_PORT:9050" \
    -e "TOR_CONTROL_PASSWORD=${TOR_CONTROL_PASSWORD:-ognon-radar-ctrl}" \
    --entrypoint /bin/sh \
    "$TOR_IMAGE" \
    -c 'exec tor --RunAsDaemon 0 --SocksPort 0.0.0.0:9050 --ControlPort 9051 --HashedControlPassword "$(tor --hash-password "${TOR_CONTROL_PASSWORD:-ognon-radar-ctrl}" | grep '\''^16:'\'')" --MaxCircuitDirtiness 60'
}

# Poll the Tor SOCKS5 proxy (published on the host) until it answers through
# the Tor network. Replaces the compose `healthcheck` + `depends_on` for tor.
wait_for_tor() {
  log "Waiting for Tor SOCKS5 proxy on 127.0.0.1:$TOR_SOCKS_PORT to be healthy..."
  local i
  for i in $(seq 1 90); do
    if curl --socks5-hostname 127.0.0.1:"$TOR_SOCKS_PORT" --fail --max-time 10 \
        https://check.torproject.org/ >/dev/null 2>&1; then
      log "Tor is healthy (attempt $i)"
      return 0
    fi
    sleep 2
  done
  warn "Tor did not become healthy within timeout — dumping logs:"
  container logs "$TOR_CONTAINER" 2>&1 | tail -n 40 || true
  return 1
}

# Poll the API health endpoint until it answers.
wait_for_api() {
  log "Waiting for the API on http://localhost:$API_PORT/api/v1/health ..."
  local i
  for i in $(seq 1 60); do
    if curl -fsS --max-time 5 "http://localhost:$API_PORT/api/v1/health" >/dev/null 2>&1; then
      log "API is ready (attempt $i)"
      return 0
    fi
    sleep 2
  done
  warn "API did not become ready within timeout — dumping logs:"
  container logs "$API_CONTAINER" 2>&1 | tail -n 40 || true
  return 1
}

# Ensure the host data directories exist (they are bind-mounted into the
# containers). Compose created them implicitly; we do it explicitly here, and
# open write access for the container uid 1000 (Docker Desktop masks host
# permissions, Apple's virtiofs may not).
ensure_data_dirs() {
  mkdir -p "$REPO_ROOT/ognon-jobs" "$REPO_ROOT/crawler_output"
  chmod a+rwX "$REPO_ROOT/ognon-jobs" "$REPO_ROOT/crawler_output"
}
