#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   scripts/deploy_live.sh [branch]
# Default branch is main.

APP_DIR="${APP_DIR:?APP_DIR is required}"
BRANCH="${1:-main}"
VENV_DIR="$APP_DIR/.venv"
SYSTEMD_SERVICE="ognon-radar-api.service"
DEPLOY_USER="${DEPLOY_USER:-$(id -un)}"
SYSTEMD_UNIT_PATH="/etc/systemd/system/$SYSTEMD_SERVICE"
CONFIG_PATH="$APP_DIR/config.yaml"

if [[ -f "$APP_DIR/config.live.yaml" ]]; then
  CONFIG_PATH="$APP_DIR/config.live.yaml"
fi

run_sudo() {
  if ! sudo -n "$@"; then
    rc=$?
    # systemctl status returns non-zero when unit is not active; this is not a sudo auth issue.
    if [[ "$1" == "systemctl" && "${2:-}" == "status" ]]; then
      return "$rc"
    fi
    echo "ERROR: sudo failed for command: $*"
    echo "Grant NOPASSWD at least for:"
    echo "- /usr/bin/tee /etc/systemd/system/$SYSTEMD_SERVICE"
    echo "- /bin/systemctl daemon-reload"
    echo "- /bin/systemctl enable $SYSTEMD_SERVICE"
    echo "- /bin/systemctl restart $SYSTEMD_SERVICE"
    echo "- /bin/systemctl status $SYSTEMD_SERVICE"
    exit 1
  fi
}

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "ERROR: $APP_DIR is not a git repository"
  exit 1
fi

cd "$APP_DIR"

echo "[deploy] Fetching latest refs"
git fetch --prune origin

echo "[deploy] Resetting working tree to origin/$BRANCH"
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"
git clean -fd

echo "[deploy] Injecting deploy indicator into frontend"
HASH="$(git rev-parse HEAD)"
HUE="$((16#${HASH:0:6} % 360))"
SHORT="${HASH:0:7}"
DATE="$(date -u +'%d/%m/%Y à %Hh%M UTC')"
sed -i "s|title=\"Développement local\"|style=\"background: hsl($HUE, 60%, 52%)\" title=\"Déployé le $DATE — $SHORT\"|" "$APP_DIR/clients/www/index.html"

echo "[deploy] Ensuring Python virtual environment"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
fi

if [[ ! -x "$VENV_DIR/bin/pip" ]]; then
  echo "[deploy] Bootstrapping pip inside virtual environment"
  "$VENV_DIR/bin/python" -m ensurepip --upgrade || true
fi

echo "[deploy] Installing dependencies"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r requirements.txt

echo "[deploy] Checking Playwright Chromium browser"
# cd "$APP_DIR" was already executed above, so the relative path is safe
if "$VENV_DIR/bin/python" scripts/check_playwright.py --verbose 2>&1; then
    echo "[deploy] Playwright Chromium already installed (version matches), skipping"
else
    cat <<'INFO'
  ┌─ Playwright Chromium is missing ──────────────────────────────────┐
  │                                                                   │
  │  The deploy script will now:                                      │
  │    1. Install system libraries via sudo apt-get  (requires sudo)  │
  │    2. Download ~200 MB of Chromium binaries    (30-60s typical)   │
  │                                                                   │
  │  If sudo prompts for a password, grant NOPASSWD for apt-get:      │
  │    echo '<USER> ALL=(ALL) NOPASSWD: /usr/bin/apt-get'             │
  │    | sudo tee /etc/sudoers.d/playwright                           │
  │                                                                   │
  └───────────────────────────────────────────────────────────────────┘
INFO
    echo "[deploy] Installing Playwright system dependencies"
    "$VENV_DIR/bin/python" -m playwright install-deps chromium || {
        rc=$?
        echo "ERROR: Failed to install Playwright system dependencies (exit code $rc)"
        echo "  This usually requires sudo/root for apt-get."
        echo "  Check your NOPASSWD sudo rules (see info box above)."
        exit $rc
    }
    echo "[deploy] Downloading and installing Chromium browser binary"
    "$VENV_DIR/bin/python" -m playwright install chromium || {
        rc=$?
        echo "ERROR: Failed to download/install Playwright Chromium (exit code $rc)"
        echo "  Check network connectivity and disk space (~200 MB required)."
        exit $rc
    }
    echo "[deploy] Playwright Chromium installed successfully"
fi

echo "[deploy] Verifying application import"
APP_CONFIG_PATH="$CONFIG_PATH" "$VENV_DIR/bin/python" - <<'PY'
import main
print(f"[deploy] Import OK: {main.settings.api_title}")
print(f"[deploy] Job DB: {main.settings.job_db_path}")
print(f"[deploy] Webhook DB: {main.settings.webhook_db_path}")
PY

echo "[deploy] Restarting systemd service: $SYSTEMD_SERVICE"
echo "[deploy] Writing systemd unit for user: $DEPLOY_USER"
run_sudo tee "$SYSTEMD_UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=Ognon Radar FastAPI
After=network.target tor.service
Requires=tor.service

[Service]
Type=simple
User=$DEPLOY_USER
WorkingDirectory=$APP_DIR
Environment=APP_CONFIG_PATH=$CONFIG_PATH
ExecStart=$VENV_DIR/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-level info
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

run_sudo systemctl daemon-reload
run_sudo systemctl enable "$SYSTEMD_SERVICE" >/dev/null
run_sudo systemctl restart "$SYSTEMD_SERVICE"
sudo -n systemctl status "$SYSTEMD_SERVICE" | sed -n '1,18p' || true

echo "[deploy] Waiting for API readiness"
ready=0
for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done

if [[ "$ready" -ne 1 ]]; then
  echo "ERROR: API did not become ready on 127.0.0.1:8000 within timeout"
  sudo -n systemctl status "$SYSTEMD_SERVICE" | sed -n '1,60p' || true
  exit 1
fi

echo "[deploy] Done"
