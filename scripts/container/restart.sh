#!/usr/bin/env bash
# Apple Container equivalent of: docker-compose restart api
# (`container` has no restart subcommand, so this is stop + start.)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

require_container

if ! container_exists "$API_CONTAINER"; then
  die "Container '$API_CONTAINER' does not exist. Start the stack first (scripts/container/up.sh)."
fi

log "Restarting '$API_CONTAINER'"
container stop "$API_CONTAINER"
container start "$API_CONTAINER"
wait_for_api
