#!/usr/bin/env bash
# Apple Container equivalent of: docker-compose up -d (--build)
#
#   scripts/container/up.sh            # build images + start tor & api
#   scripts/container/up.sh --no-build # reuse already-built images
#   scripts/container/up.sh --locust   # also start the locust profile
#
# Order follows docker-compose.yml:
#   1. build the API image (the crawler image is built on demand by
#      scripts/container/crawler.sh, like compose builds profile services
#      only when the profile is enabled)
#   2. create the darkweb-net network
#   3. run tor (SOCKS5 :9050) and wait for it to be healthy
#      (replaces healthcheck + depends_on: service_healthy)
#   4. run the api (published :8337 -> 8000)
#   5. optionally start the locust profile service

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

DO_BUILD=1
START_LOCUST=0
for arg in "$@"; do
  case "$arg" in
    --no-build) DO_BUILD=0 ;;
    --locust)   START_LOCUST=1 ;;
    -h|--help)
      grep '^#' "$0" | sed -n '2,11p'; exit 0 ;;
    *) die "Unknown option: $arg (expected: --no-build | --locust)" ;;
  esac
done

require_container
ensure_data_dirs

# ── 1. Build / pull images ──────────────────────────────────────────────────
if [[ "$DO_BUILD" -eq 1 ]]; then
  log "Building API image ($API_IMAGE) from Dockerfile"
  ( cd "$REPO_ROOT" && container build -t "$API_IMAGE" -f Dockerfile . )
else
  log "--no-build: reusing existing images"
  if ! container image inspect "$API_IMAGE" >/dev/null 2>&1; then
    die "Image $API_IMAGE is missing and --no-build was passed. Run without --no-build to build it first."
  fi
fi

# Tor & locust images are pulled automatically on first run.

# ── 2. Network ──────────────────────────────────────────────────────────────
ensure_network

# ── 3. Tor ──────────────────────────────────────────────────────────────────
ensure_tor
wait_for_tor

# ── 4. API ──────────────────────────────────────────────────────────────────
# Bind mounts from docker-compose.yml, reduced to the two directory mounts.
# IMPORTANT (CLI v1.2.x quirk): Apple's `container` silently DROPS all -v
# mounts if any of them has a FILE as its source (directories only). The
# compose file mounted ./config.yaml:/app/config.yaml separately — here that
# file is already covered by the $REPO_ROOT:/app mount, so the separate file
# mount is removed (config.yaml is inside the repo; the app only reads it,
# so dropping the compose `:ro` suffix is harmless).
# The image runs as apiuser (uid 1000); we pass --user 1000 to match Docker's
# behaviour of honouring the image USER directive.
# The vmnet network does not resolve container names, so we pass tor's real IP
# to the app via TOR_PROXY / TOR_CONTROL_HOST (config.py prefers these env vars
# over the `tor:9050` value in config.yaml, which only works under Docker DNS).
TOR_IP="$(container_ip "$TOR_CONTAINER")"
[[ -n "$TOR_IP" ]] || die "Could not resolve the IP of container '$TOR_CONTAINER'"
TOR_PROXY_VALUE="socks5://$TOR_IP:$TOR_SOCKS_PORT"

start_api() {
  log "Starting API container '$API_CONTAINER' (published :$API_PORT -> 8000)"
  run_detached "$API_CONTAINER" \
    --network "$NETWORK" \
    -p "$API_PORT:8000" \
    --user 1000 \
    --ulimit nofile=4096:8192 \
    -e "TOR_PROXY=$TOR_PROXY_VALUE" \
    -e "TOR_CONTROL_HOST=$TOR_IP" \
    -v "$REPO_ROOT:/app" \
    -v "$REPO_ROOT/ognon-jobs:/app/data" \
    "$API_IMAGE" \
    uvicorn main:app --host 0.0.0.0 --port 8000
}

if ! container_exists "$API_CONTAINER"; then
  start_api
else
  EXISTING_PROXY="$(container_env "$API_CONTAINER" TOR_PROXY)"
  # Start it first so the liveness probe below can run, then recreate when the
  # env is stale or the /app bind mount is not live (a CLI v1.2.x quirk can
  # silently drop all directory mounts when a file-source -v is present — the
  # container would otherwise run on the image's baked filesystem).
  container start "$API_CONTAINER" 2>/dev/null || true
  if [[ "$EXISTING_PROXY" != "$TOR_PROXY_VALUE" ]] || ! container_mount_live "$API_CONTAINER" /app/.git; then
    warn "'$API_CONTAINER' has a stale Tor IP or a dead /app mount — recreating container"
    container rm -f "$API_CONTAINER" >/dev/null
    start_api
  else
    log "Container '$API_CONTAINER' already exists and is healthy — nothing to do"
  fi
fi

wait_for_api

# ── 5. Optional profiles ────────────────────────────────────────────────────
if [[ "$START_LOCUST" -eq 1 ]]; then
  "$SCRIPT_DIR/locust.sh"
fi

log "Stack is up:"
log "  API   → http://localhost:$API_PORT/docs"
log "  Tor   → socks5://127.0.0.1:$TOR_SOCKS_PORT"
if [[ "$START_LOCUST" -eq 1 ]]; then
  log "  Locust → http://localhost:$LOCUST_PORT"
fi
log "Crawler (one-shot): scripts/container/crawler.sh python dark_crawler.py -u http://<onion> --json"
log "Stop everything with: scripts/container/down.sh"
