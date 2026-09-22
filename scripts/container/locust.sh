#!/usr/bin/env bash
# Apple Container equivalent of:
#   docker-compose --profile testing up -d    (locust service)
#
# Starts the Locust load-testing UI on http://localhost:8089, pointed at the
# api container (http://api:8000 over the darkweb-net network).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

require_container
ensure_network

if ! container_exists "$LOCUST_CONTAINER"; then
  log "Starting Locust container '$LOCUST_CONTAINER' (http://localhost:$LOCUST_PORT)"
  container run -d \
    --name "$LOCUST_CONTAINER" \
    --network "$NETWORK" \
    -p "$LOCUST_PORT:8089" \
    -v "$REPO_ROOT:/mnt/locust" \
    "$LOCUST_IMAGE" \
    -f /mnt/locust/locustfile.py --host http://api:8000
else
  log "Container '$LOCUST_CONTAINER' already exists — starting it if stopped"
  container start "$LOCUST_CONTAINER" 2>/dev/null || true
fi

log "Locust UI: http://localhost:$LOCUST_PORT"
