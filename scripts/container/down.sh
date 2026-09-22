#!/usr/bin/env bash
# Apple Container equivalent of: docker-compose down
#
#   scripts/container/down.sh              # stop + remove the stack's containers
#   scripts/container/down.sh --clean      # also delete the darkweb-net network
#
# Note: like `docker-compose down`, this removes the containers but keeps the
# data in ./ognon-jobs and ./crawler_output.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

CLEAN_NETWORK=0
for arg in "$@"; do
  case "$arg" in
    --clean) CLEAN_NETWORK=1 ;;
    -h|--help) grep '^#' "$0" | sed -n '2,5p'; exit 0 ;;
    *) warn "Unknown option: $arg (ignored)"; exit 1 ;;
  esac
done

require_container

containers=("$API_CONTAINER" "$TOR_CONTAINER" "$CRAWLER_CONTAINER" "$LOCUST_CONTAINER")
for name in "${containers[@]}"; do
  if container_exists "$name"; then
    log "Stopping and removing '$name'"
    container stop "$name"  2>/dev/null || true
    container delete "$name" 2>/dev/null || true
  fi
done

if [[ "$CLEAN_NETWORK" -eq 1 ]] && container network list -q 2>/dev/null | grep -qx "$NETWORK"; then
  log "Deleting network '$NETWORK'"
  container network delete "$NETWORK" 2>/dev/null || warn "Could not delete network '$NETWORK' (in use?)"
fi

log "Done."
