#!/usr/bin/env bash
# Start the container engine backing `make up` / `make up-build` if it is not
# already running. Called by the Makefile (target `_ensure-engine`) with the
# runtime it detected (container | docker), or standalone:
#
#   scripts/ensure-engine.sh            # auto-detect, then start
#   scripts/ensure-engine.sh docker     # start the docker engine only
#   scripts/ensure-engine.sh container  # start the Apple container services
#
# The three supported engines, in the order the Makefile prefers them:
#   - container → Apple `container` CLI  : `container system start`
#   - colima    → Docker Desktop is absent, `colima start` (daemon + context)
#   - docker    → Docker Desktop (or any running docker daemon): `open -a Docker`
#
# Everything is a no-op (and stays silent) when the engine already answers, so
# `make up` stays fast in the common case.

set -uo pipefail

# Seconds to wait for the daemon to answer after starting it (Docker Desktop
# is the slowest to boot; override with ENGINE_WAIT_TIMEOUT=… if needed).
WAIT_TIMEOUT="${ENGINE_WAIT_TIMEOUT:-150}"
readonly WAIT_TIMEOUT

if [[ -t 1 ]]; then
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_RED=$'\033[31m'
  C_RESET=$'\033[0m'
else
  C_GREEN="" C_YELLOW="" C_RED="" C_RESET=""
fi

log()  { printf '%s[engine]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s[engine]%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
die()  { printf '%s[engine]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

# Poll `check` every 2s until it succeeds or WAIT_TIMEOUT is reached.
wait_for() {
  local check="$1" label="$2" waited=0
  while (( waited < WAIT_TIMEOUT )); do
    if $check; then
      return 0
    fi
    sleep 2
    waited=$(( waited + 2 ))
  done
  warn "$label did not become ready after ${WAIT_TIMEOUT}s."
  return 1
}

# ── Apple `container` ───────────────────────────────────────────────────────
# `container system status` exits non-zero while the services are down, but
# the wording also changed between releases, so both are checked.
container_running() {
  local out
  out="$(container system status 2>&1)" || return 1
  [[ "$out" != *"not running"* ]]
}

ensure_container() {
  have container || die "The 'container' CLI was not found (RUNTIME=container was requested).
  Install it from https://github.com/apple/container/releases (macOS 26+, Apple silicon)."
  container_running && return 0
  log "Apple container services are stopped — starting them (container system start)..."
  container system start || die "'container system start' failed."
  wait_for container_running "Apple container services" \
    || die "Apple container services still down. Check: container system status"
}

# ── docker daemon (Docker Desktop or Colima) ────────────────────────────────
docker_running() { docker info >/dev/null 2>&1; }

ensure_docker() {
  have docker || die "The 'docker' CLI was not found (RUNTIME=docker was requested).
  Install Docker Desktop or Colima: https://docs.docker.com/desktop/install/mac-install/"
  docker_running && return 0

  if have colima; then
    log "The docker daemon is down and Colima is installed — starting it (colima start)..."
    colima start || die "'colima start' failed. Check: colima status"
    wait_for docker_running "Colima" \
      || die "Colima did not come up. Check: colima status"
    return 0
  fi

  # No Colima: the daemon can only be a desktop app (Docker Desktop, or
  # Rancher/Podman Desktop exposing a docker-compatible context).
  local app
  for app in Docker Rancher\ Desktop Podman\ Desktop OrbStack; do
    if [[ -d "/Applications/${app}.app" ]]; then
      log "The docker daemon is down — launching ${app}.app, this can take a while..."
      open -a "${app}"
      wait_for docker_running "The docker daemon (${app})" \
        || die "The docker daemon still does not answer. Start ${app}.app manually and retry."
      return 0
    fi
  done

  die "The docker daemon is down and no way to start it was found.
  Start Docker Desktop (or run 'colima start'), then retry."
}

# ── Entry point ─────────────────────────────────────────────────────────────
RUNTIME="${1:-auto}"
case "$RUNTIME" in
  auto)
    # Same precedence as the Makefile: the Apple CLI wins when installed.
    if have container; then
      ensure_container
    elif have docker || have colima; then
      ensure_docker
    else
      die "No container backend detected on this machine.
  Install Docker (Docker Desktop / Colima) or the Apple 'container' CLI
  (macOS 26+, https://github.com/apple/container/releases)."
    fi
    ;;
  container) ensure_container ;;
  docker)    ensure_docker ;;
  *)         die "Unknown runtime '$RUNTIME' (expected: auto | docker | container)" ;;
esac