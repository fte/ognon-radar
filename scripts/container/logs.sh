#!/usr/bin/env bash
# Apple Container equivalent of: docker-compose logs -f <service>
#
#   scripts/container/logs.sh            # tail the API logs
#   scripts/container/logs.sh tor        # tail the Tor logs
#   scripts/container/logs.sh crawler    # tail the crawler logs (if started)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

service="${1:-api}"
case "$service" in
  api)     name="$API_CONTAINER" ;;
  tor)     name="$TOR_CONTAINER" ;;
  crawler) name="$CRAWLER_CONTAINER" ;;
  locust)  name="$LOCUST_CONTAINER" ;;
  -h|--help)
    grep '^#' "$0" | sed -n '2,6p'; exit 0 ;;
  *) die "Unknown service '$service' (expected: api | tor | crawler | locust)" ;;
esac

require_container

if ! container_exists "$name"; then
  die "Container '$name' does not exist. Start the stack first (scripts/container/up.sh)."
fi

container logs -f "$name"
