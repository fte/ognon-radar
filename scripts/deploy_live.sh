#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   scripts/deploy_live.sh [branch]
# Default branch is main.

APP_DIR="/home/gbp4dt5/zones/13h.be/api.dw"
BRANCH="${1:-main}"
VENV_DIR="$APP_DIR/.venv"
SYSTEMD_SERVICE="ognon-radar-api.service"
DEPLOY_USER="${DEPLOY_USER:-$(id -un)}"
SYSTEMD_UNIT_PATH="/etc/systemd/system/$SYSTEMD_SERVICE"

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "ERROR: $APP_DIR is not a git repository"
  exit 1
fi

cd "$APP_DIR"

echo "[deploy] Fetching latest refs"
git fetch --prune origin

echo "[deploy] Checking out $BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

echo "[deploy] Ensuring Python virtual environment"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
fi

echo "[deploy] Installing dependencies"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r requirements.txt

echo "[deploy] Restarting systemd service: $SYSTEMD_SERVICE"
if ! sudo -n true 2>/dev/null; then
  echo "ERROR: passwordless sudo is required for deployment user"
  echo "Grant at least: /bin/systemctl daemon-reload, restart, status"
  exit 1
fi

echo "[deploy] Writing systemd unit for user: $DEPLOY_USER"
sudo -n tee "$SYSTEMD_UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=Ognon Radar FastAPI
After=network.target tor.service
Requires=tor.service

[Service]
Type=simple
User=$DEPLOY_USER
WorkingDirectory=$APP_DIR
Environment=APP_CONFIG_PATH=$APP_DIR/config.yaml
ExecStart=$VENV_DIR/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --log-level info
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo -n systemctl daemon-reload
sudo -n systemctl enable "$SYSTEMD_SERVICE" >/dev/null
sudo -n systemctl restart "$SYSTEMD_SERVICE"
sudo -n systemctl --no-pager --full status "$SYSTEMD_SERVICE" | sed -n '1,18p'

echo "[deploy] Health probe"
curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null

echo "[deploy] Done"
