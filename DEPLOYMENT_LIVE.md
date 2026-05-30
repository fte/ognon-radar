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
<DEPLOY_USER> ALL=(root) NOPASSWD:/usr/bin/tee /etc/systemd/system/ognon-radar-api.service,/bin/systemctl daemon-reload,/bin/systemctl enable ognon-radar-api.service,/bin/systemctl restart ognon-radar-api.service,/bin/systemctl status ognon-radar-api.service
EOF
chmod 440 /etc/sudoers.d/ognon-radar-deploy
```

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

Important: `User=` is set from the SSH deployment user at runtime (the same account as GitHub secret `VPS_USER`).

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
- `VPS_USER` = SSH user used for deploy (also used as `User=` in systemd unit)
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

