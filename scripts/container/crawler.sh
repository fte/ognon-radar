#!/usr/bin/env bash
# Apple Container equivalent of:
#   docker-compose --profile crawler run --rm crawler python dark_crawler.py -u http://<onion> --json
#
# Usage (arguments are passed through to the container):
#   scripts/container/crawler.sh python dark_crawler.py -u http://xxx.onion --json
#
# The crawler image is built on first use (compose only builds profile
# services when the profile is enabled). Tor is started if needed so output
# goes through the Tor network like in docker-compose.yml.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

if [[ $# -eq 0 || "$1" == "-h" || "$1" == "--help" ]]; then
  grep '^#' "$0" | sed -n '2,9p'
  # No command given → fail loudly instead of silently doing nothing
  # (make crawler without ARGS must not "succeed").
  [[ $# -eq 0 ]] && exit 1
  exit 0
fi

require_container
ensure_data_dirs
ensure_network

# Build the crawler image if it does not exist yet (build is cached, so this
# is a no-op on subsequent runs once the image is present).
if ! container image inspect "$CRAWLER_IMAGE" >/dev/null 2>&1; then
  log "Building crawler image ($CRAWLER_IMAGE) from Dockerfile.crawler"
  ( cd "$REPO_ROOT" && container build -t "$CRAWLER_IMAGE" -f Dockerfile.crawler . )
fi

# Make sure tor is up (crawling goes through the Tor network).
ensure_tor
wait_for_tor

# Same DNS limitation as the api container: pass tor's real IP via
# TOR_PROXY (dark_crawler.py reads it; default would be 'socks5h://tor:9050'
# which only resolves under Docker's embedded DNS).
TOR_IP="$(container_ip "$TOR_CONTAINER")"
[[ -n "$TOR_IP" ]] || die "Could not resolve the IP of container '$TOR_CONTAINER'"

log "Running crawler (${CRAWLER_IMAGE}): $*"
container run --rm \
  --name "$CRAWLER_CONTAINER" \
  --network "$NETWORK" \
  -e "TOR_PROXY=socks5://$TOR_IP:$TOR_SOCKS_PORT" \
  -v "$REPO_ROOT:/app" \
  -v "$REPO_ROOT/crawler_output:/app/output" \
  "$CRAWLER_IMAGE" \
  "$@"
