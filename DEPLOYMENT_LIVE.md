# Live Deployment Runbook (VPS, no Docker)

Use placeholders only in this document.
Do not commit real hostnames, usernames, domains, IPs, or absolute paths.

Target template:
- Host: `<VPS_HOST>`
- Deploy user: `<DEPLOY_USER>`
- App path: `<APP_DIR>`
- API FQDN: `<API_FQDN>`

## 1) One-time setup on VPS

```bash
# As root
apt-get update
apt-get install -y git python3 python3-venv python3-pip nginx certbot python3-certbot-nginx tor
systemctl enable --now nginx tor
```

Allow deploy user to restart service without password prompt:

```bash
cat >/etc/sudoers.d/ognon-radar-deploy <<'EOF'
<DEPLOY_USER> ALL=(root) NOPASSWD:/usr/bin/tee /etc/systemd/system/ognon-radar-api.service,/bin/systemctl daemon-reload,/bin/systemctl enable ognon-radar-api.service,/bin/systemctl restart ognon-radar-api.service,/bin/systemctl status ognon-radar-api.service,/usr/bin/apt-get
EOF
chmod 440 /etc/sudoers.d/ognon-radar-deploy
```

The `/usr/bin/apt-get` rule is required by the deploy script: when
`scripts/check_playwright.py` detects a browser whose system libraries are
missing, it runs `playwright install-deps chromium`, which invokes apt-get
as root.

Tor should listen on `127.0.0.1:9050`.

```bash
ss -lntp | grep 9050
```

## 2) App bootstrap

```bash
su - <DEPLOY_USER>
mkdir -p "<APP_PARENT_DIR>"
cd "<APP_PARENT_DIR>"
git clone <REPO_URL> <APP_DIR_BASENAME>
cd "<APP_DIR_BASENAME>"

# Use production config (host tor + local db path)
cp config.live.yaml config.yaml
mkdir -p data
```

## 3) Systemd service

No manual file is required: `scripts/deploy_live.sh` writes and updates
`/etc/systemd/system/ognon-radar-api.service` automatically.

Important: `User=` is set from the SSH deployment user at runtime (the same account as GitHub secret `VPS_SSH_USER`).

The script also runs:
- `systemctl daemon-reload`
- `systemctl enable ognon-radar-api.service`
- `systemctl restart ognon-radar-api.service`

## 4) First deploy

```bash
cd "<APP_DIR>"
chmod +x scripts/deploy_live.sh
./scripts/deploy_live.sh main
```

## 5) Nginx reverse proxy

Your current block is good. Keep proxy to `127.0.0.1:8000` and add proto header:

```nginx
server {
    listen 80;
  server_name <API_FQDN>;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and reload:

```bash
ln -sf /etc/nginx/sites-available/<API_FQDN> /etc/nginx/sites-enabled/<API_FQDN>
nginx -t && systemctl reload nginx
```

## 6) TLS

```bash
certbot --nginx -d <API_FQDN>
```

## 7) GitHub Actions secrets (repo settings)

- `VPS_HOST` = `<VPS_HOST>`
- `VPS_SSH_USER` = SSH user used for deploy (also used as `User=` in systemd unit)
- `VPS_SSH_KEY` = private key content used by GitHub Actions
- `VPS_SSH_PORT` = `<VPS_SSH_PORT>` (usually 22)

Workflow file: `.github/workflows/deploy-live.yml`

## 8) Deploy flow

- Push to `main` triggers live deploy.
- Manual deploy possible via `workflow_dispatch`.
- Remote script executed: `scripts/deploy_live.sh`:
  - git pull
  - ensure `.venv`
  - pip install requirements
  - restart `ognon-radar-api.service`
  - local health probe

## 9) Post-deploy checks

```bash
# On VPS
systemctl --no-pager --full status ognon-radar-api.service
journalctl -u ognon-radar-api.service -n 100 --no-pager
curl -fsS http://127.0.0.1:8000/api/v1/health

# From anywhere
curl -fsS https://<API_FQDN>/api/v1/health
curl -fsS https://<API_FQDN>/docs >/dev/null
```

## 10) Troubleshooting — screenshot jobs fail at browser launch

Symptom (job error visible on `GET /api/v1/jobs/{job_id}`, surfaced as an
XHR failure in the web client):

```
BrowserType.launch: Target page, context or browser has been closed
... error while loading shared libraries: libasound.so.2:
cannot open shared object file: No such file or directory
```

Cause: `playwright install chromium` only downloads the browser binary;
OS-level libraries come from `playwright install-deps chromium` (apt-get).
The binary is present, so revision checks pass, but it cannot load — every
screenshot job dies at `BrowserType.launch`.

One-time fix on the VPS:

```bash
# Diagnose: lists the exact libraries the binary cannot load
<APP_DIR>/.venv/bin/python scripts/check_playwright.py --verbose

# Install the missing OS libraries (as root, or as deploy user with the
# apt-get NOPASSWD rule from step 1)
sudo <APP_DIR>/.venv/bin/python -m playwright install-deps chromium

sudo systemctl restart ognon-radar-api.service
```

Then resubmit the screenshot job — failed jobs are terminal, there is no
server-side retry endpoint (only webhook deliveries have one).

Note: future deploys self-heal this (the deploy script runs the same check
and installs deps automatically), but only if the apt-get sudo rule above
is in place — otherwise the deploy aborts with "Failed to install Playwright
system dependencies".

### Screenshot jobs fail with "Screenshot failed for <url>" (browser launches)

If the browser now launches but every screenshot still fails while the
reachability probe (curl/httpx through the same Tor proxy) succeeds, check
which proxy Chromium is being given.  ``core/screenshot.py`` resolves it
from ``TOR_PROXY`` (scripts/container) or ``tor.proxy`` in the active
config, and normalizes ``socks5h://`` to ``socks5://`` (the only SOCKS
scheme Chromium's ``--proxy-server`` understands; Chrome always resolves
names proxy-side anyway).

A historical bug shipped a hard-coded default of ``socks5://tor:9050``:
under systemd there is no ``TOR_PROXY`` env and the ``tor`` hostname does
not resolve on a native VPS, so navigation failed in every job.  Verify the
resolution with the production config:

```bash
APP_CONFIG_PATH="$APP_DIR/config.live.yaml" \
  "$APP_DIR/.venv/bin/python" -c \
  'from config import settings; from core.screenshot import _resolve_proxy_url; print(settings.tor_proxy, "->", _resolve_proxy_url())'
```

Expected on the VPS: ``socks5h://127.0.0.1:9050 -> socks5://127.0.0.1:9050``.
If it prints ``tor:9050``, the service is not using ``config.live.yaml`` —
check the ``APP_CONFIG_PATH`` env in the systemd unit.

