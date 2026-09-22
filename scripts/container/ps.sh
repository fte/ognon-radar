#!/usr/bin/env bash
# Apple Container equivalent of: docker-compose ps

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

require_container
container list -a
