#!/usr/bin/env bash
# Apple Container equivalent of: docker-compose exec api /bin/bash
#
# Usage: scripts/container/shell.sh [command...]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

require_container

if ! container_exists "$API_CONTAINER"; then
  die "Container '$API_CONTAINER' does not exist. Start the stack first (scripts/container/up.sh)."
fi

if [[ $# -eq 0 ]]; then
  container exec -it "$API_CONTAINER" /bin/bash
else
  container exec -it "$API_CONTAINER" "$@"
fi
