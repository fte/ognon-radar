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

## 11) Troubleshooting — SSE dies on mobile Chrome ("error / reconnect...")

Symptom: the web client's *Réseau · XHR* panel fills with
`SSE … error · reconnect…` lines, and the search stays frozen even though the
job finishes server-side. Desktop is usually fine; mobile Chrome is not.

Two independent causes, both fixed in this repo — but check them if you are on
an older deploy:

### 11.1 Rate-limit bucket collapsed onto `127.0.0.1`

`uvicorn` is started **without** `--proxy-headers`, so `request.client.host` is
`127.0.0.1` for every request behind nginx. `EventSource` cannot send custom
headers, so `X-Client-ID` is absent on SSE and `_rate_limit_key` fell back to
that peer address: **every SSE stream on the internet shared one
`5/minute` bucket** on `GET /api/v1/jobs/{id}/stream`.

Once exhausted, slowapi returns `429`. Per the HTML spec a reconnect that gets
a non-`2xx` / non-`text/event-stream` response fails the EventSource
**permanently** (`readyState === CLOSED`), which is what froze the UI.

`core/rate_limiter.py` now resolves the real client IP from `X-Real-IP`, then
from the **rightmost** `X-Forwarded-For` entry — and only when the direct peer
is loopback/RFC1918, so a direct client cannot forge its IP to dodge the
limiter. Reading the *leftmost* XFF entry would be the trap: nginx's
`$proxy_add_x_forwarded_for` prepends whatever the client sent, so the
leftmost value is attacker-controlled and would hand each caller a
self-selected bucket. The rightmost entry is nginx's own `$remote_addr`.

This depends on nginx forwarding both headers. Confirm the block that actually
answers your FQDN — see §5.1 below.

### 5.1 Verifying the live vhost

There is **no nginx config in this repo**; it lives only on the VPS. Use
`nginx -T`, not `cat`, because it prints the *resolved* configuration of every
file including anything pulled in by `include`:

```bash
# 1. Which file actually serves <API_FQDN>?
nginx -T 2>/dev/null | grep -n "server_name\|configuration file"

# 2. Are the two headers present in that server block?
nginx -T 2>/dev/null | grep -n "proxy_set_header X-Real-IP\|proxy_set_header X-Forwarded-For"

# 3. Confirm the API really receives them (does not need config access).
curl -sS -D- -o/dev/null https://<API_FQDN>/api/v1/health | grep -i '^x-real-ip\|^x-forwarded-for'
```

Expected from step 3: `x-real-ip: <your public IP>`. If you get
`x-real-ip: 127.0.0.1`, the header is not being set by the active vhost.

Beware of two things that make this confusing:

- **`sites-enabled/<FQDN>` is a symlink** to `sites-available/<FQDN>`. `cat`ing
  the wrong file, or a second `server` block earlier in the file winning on
  `default_server`, will show you a block that is not in effect.
- **certbot rewrites the file.** `certbot --nginx` splits the block in two and
  duplicates the `location /` directives — once for `:80` and once for the
  `:443` TLS server. Check the **`:443`** block: that is the one mobile Chrome
  actually hits, and it is easy to fix `:80` while leaving `:443` untouched.

A one-line sanity check of the API's own view of the caller:

```bash
APP_CONFIG_PATH="$APP_DIR/config.live.yaml" "$APP_DIR/.venv/bin/python" -c \
  'from fastapi import Request; from core.rate_limiter import get_remote_address
from unittest.mock import MagicMock
r = MagicMock(spec=Request)
r.headers = {"X-Real-IP": "203.0.113.7"}
r.client = MagicMock(); r.client.host = "127.0.0.1"
print(get_remote_address(r))'
```

Expected: `203.0.113.7`. If it prints `127.0.0.1`, the request never went
through nginx (or nginx isn't setting the header).

#### Trust boundary: which peers may assert a client IP

`core/rate_limiter._is_trusted_proxy()` decides whether a forwarded header is
believable, based on the **direct peer's** address. Getting this wrong is
expensive in both directions: too narrow and every client collapses back into
one shared bucket (the original bug); too wide and a peer can forge its own
bucket and walk straight through the limiter.

The default set is loopback + full RFC1918 (`10/8`, `172.16/12`,
`192.168/16`) + `169.254/16` + IPv6 loopback/ULA/link-local, with
IPv4-mapped IPv6 (`::ffff:127.0.0.1`) unwrapped. That last case matters:
uvicorn reports the mapped form when it binds a dual-stack socket, so a
perfectly local proxy would otherwise look untrusted.

**Known trade-off of that default:** if the API is reachable directly from a
LAN with no proxy in front, any host on `192.168.x.x` can send
`X-Real-IP: <anything>` and pick its own bucket. For that exposure, pin the
boundary explicitly:

```yaml
security:
  trusted_proxies: ["127.0.0.1/32", "::1/128"]   # loopback only
```

When `security.trusted_proxies` is non-empty it **replaces** the default
entirely. Invalid entries are logged and skipped; if none parse, the defaults
are kept so a typo cannot silently disable forwarding.

Topology matrix (all verified against the current code):

| Deployment | Peer seen by the API | Result |
|---|---|---|
| Native VPS, nginx same host | `127.0.0.1` | per-client buckets |
| Docker Compose, nginx sibling on `darkweb-net` | `172.18.0.5` | per-client buckets |
| Docker Compose as shipped (no nginx, port published) | real client IP | per-client buckets, headers unused |
| Dual-stack uvicorn behind local nginx | `::ffff:127.0.0.1` | per-client buckets |
| API exposed on a LAN, no proxy | `192.168.1.50` | **headers ignored only if `trusted_proxies` is pinned** |

The `172.16/12` entry is not decorative: Docker's default bridge pools are
`172.17.0.0/16`, `172.18.0.0/16`, … An earlier `startswith` check omitted that
range, so adding an nginx container to the Compose stack would have silently
reintroduced the exact bucket collapse described in §11.1.

### 11.2 No reconnect logic on the client

`searchEs.onerror` only logged a line and let the browser handle it, so any
cancelled SSE (screen lock, 4G→5G handover, carrier NAT idle timeout) left the
job stuck forever. `clients/www/app.js` now has `openJobStream()`, shared by the
search and capture flows, which mints a **fresh stream token** per attempt and
retries with bounded backoff (2s → 10s, 6 tries). The search flow then falls
back to plain REST polling on `GET /api/v1/jobs/{id}` every 3s, so a dropped
stream can never freeze the UI again.

Each attempt is traced in the panel with the reason and the retry counter, e.g.
`SSE … error · flux fermé · hors ligne · retry 3/6 dans 8s`. A `NET` line is
logged on `online`/`offline` transitions, which pinpoints a network handover.

