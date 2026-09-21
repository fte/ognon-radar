#!/usr/bin/env bash
# Apple Container equivalent of: make test
# (docker-compose run --rm api python -m pytest tests/ -v)
#
# Usage:
#   scripts/container/test.sh                # full suite
#   scripts/container/test.sh tests/test_capture.py -v   # extra pytest args
#
# The API image must be built first (scripts/container/up.sh). Tor is started
# if it is not already running, because some tests talk to the Tor network.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

require_container
ensure_data_dirs
ensure_network

# Build the API image on first use (like crawler.sh does for its image), so
# the test suite works without having run up.sh first.
if ! container image inspect "$API_IMAGE" >/dev/null 2>&1; then
  log "Building API image ($API_IMAGE) from Dockerfile"
  ( cd "$REPO_ROOT" && container build -t "$API_IMAGE" -f Dockerfile . )
fi

# Make sure the tor container is up so network-dependent tests can run
# (compose would start the `tor` dependency the same way).
ensure_tor
wait_for_tor

# Tests run as root: pytest writes bytecode and .pytest_cache inside the
# host-mounted repository (/app), and unlike Docker Desktop, Apple's virtiofs
# does not necessarily mask host permissions for uid 1000.
log "Running pytest inside the API image (${API_IMAGE})"
container run --rm \
  --name darkweb-test \
  --network "$NETWORK" \
  -u 0 \
  --ulimit nofile=4096:8192 \
  -v "$REPO_ROOT:/app" \
  -v "$REPO_ROOT/ognon-jobs:/app/data" \
  "$API_IMAGE" \
  python -m pytest tests/ -v "$@"
