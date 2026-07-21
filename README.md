# ognon-radar

RESTful API for searching .onion (Tor hidden services) sites. Built with FastAPI and Docker.

## 🎯 Features

- **RESTful API**: Clean REST architecture with proper HTTP methods and status codes
- **Tor Integration**: All traffic routed through Tor SOCKS5 proxy for anonymity
- **BFS Crawling**: Breadth-first search algorithm for efficient site discovery
- **Search Functionality**: Find .onion sites containing specific keywords
- **Docker-First**: Everything runs in containers (API + Tor)
- **Fully Typed**: Type hints throughout for better IDE support

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- Git

### Installation

```bash
# Clone repository
git clone https://github.com/fte/ognon-radar.git
cd ognon-radar

# (Optional) Customize config.yaml with your settings

# Build and start services
docker-compose up --build
```

The API will be available at `http://localhost:8337`

## 📡 API Endpoints

### Health Check

```bash
GET /api/v1/health
```

**Response:**
```json
{
  "status": "ok",
  "tor_connected": true,
  "timestamp": "2025-12-12T10:30:00Z"
}
```

### Search .onion Sites

```bash
POST /api/v1/search
```

**Request Body:**
```json
{
  "term": "cybersec",
  "start_url": "http://xxx...xxx.onion",
  "max_results": 10,
  "max_depth": 2,
  "max_pages": 50,
  "timeout": 30
}
```

**Response:**
```json
{
  "term": "cybersec",
  "results": [
    {
      "url": "http://abc1234567abcdefghijklmnopqrstuvwxyz234567abcdefghijklmno.onion/topic/cybersec-tools",
      "title": "CyberSec Tools | Open-Source Security Toolkit",
      "snippet": "...comprehensive cybersec resource for penetration testing and auditing.",
      "timestamp": "2025-12-12T10:30:00Z",
      "seed": "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q=cybersec",
      "depth": 1,
      "term_count": 3
    },
    {
      "url": "http://xyz7654321zyxwvutsrqponmlkjihgfedcba765432zyxwvutsrqponm.onion/resources/cybersec-training/index.html",
      "title": "CyberSec Training Portal - Security Research Hub",
      "snippet": "...free cybersec training materials for authorized researchers...",
      "timestamp": "2025-12-12T10:30:00Z",
      "seed": "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/?q=cybersec",
      "depth": 1,
      "term_count": 3
    }
  ],
  "total": 5,
  "crawled_pages": 25,
  "duration_seconds": 120.5,
  "tor_connected": true
}
```

## 🔧 Configuration

All settings are in `config.yaml`:

```yaml
# Tor Configuration
tor:
  proxy: "socks5h://tor:9050"
  
# Crawling Settings
crawling:
  delay: 7              # Seconds between requests
  max_depth: 2          # Maximum crawl depth
  max_pages: 50         # Max pages to crawl
  max_results: 10       # Max search results
  timeout: 30           # Request timeout (seconds)
```

Customize by editing `config.yaml` before running `docker-compose up`

## 🐳 Docker Commands

```bash
# Start all services
docker-compose up

# Start in background
docker-compose up -d

# View logs
docker-compose logs -f api
docker-compose logs -f tor

# Stop services
docker-compose down

# Rebuild after code changes
docker-compose up --build

# Start with load testing
docker-compose --profile testing up
```

## Disk Usage Warning

The `./ognon-jobs` directory is bind-mounted into the container at `/app/data`. Capture jobs write WARC archives to `/app/data/captures` (persisted on the host under `ognon-jobs/captures/`).

**WARC files can be large.** A single capture of a multi-page site can easily produce hundreds of megabytes. Before running capture jobs at scale:

- Set a quota or `max_size_mb` limit in `config.yaml` under `capture`.
- Monitor disk usage: `du -sh ./ognon-jobs/captures/`
- Clean up completed captures manually or set a rotation policy.
- Consider mounting `ognon-jobs/` on a separate volume with a hard disk quota to prevent the host from running out of space.

## 📚 Documentation

- **Swagger UI**: http://localhost:8337/docs
- **ReDoc**: http://localhost:8337/redoc

## 🏗️ Project Structure

```
ognon-radar/
├── main.py              # FastAPI application entry point
├── config.py            # Configuration management
├── core/
│   ├── tor_client.py   # Tor SOCKS5 proxy client
│   └── crawler.py      # BFS crawler with search
├── models/
│   └── schemas.py      # Pydantic request/response models
├── routes/
│   ├── health.py       # Health check endpoint
│   └── search.py       # Search endpoint
├── requirements.txt    # Python dependencies
├── Dockerfile          # API container image
└── docker-compose.yml  # Multi-container orchestration
```

## 🛠️ Development

### Local Development with Hot Reload

```bash
docker-compose up
# Code changes will auto-reload
```

### Running Tests

This project is **Docker-only** — there is no local Python environment.
All tests run inside the API container:

```bash
make test
# or directly:
docker-compose run --rm api python -m pytest tests/ -v

# Run a specific test file:
docker-compose run --rm api python -m pytest tests/test_capture.py -v
```

### Code Style

- Follow PEP 8
- Use type hints
- Add docstrings to all functions
- Keep functions small and focused

## 🔮 Roadmap

### Phase 1: Search Functionality ✅
- [x] REST API architecture
- [x] Tor integration
- [x] BFS crawling
- [x] Search endpoint
- [x] Docker setup

### Phase 2: Screenshot Functionality (Upcoming)
- [ ] Selenium integration
- [ ] Screenshot endpoint
- [ ] Image storage
- [ ] Term highlighting

### Phase 3: Advanced Features (Future)
- [ ] Redis caching
- [ ] Celery background tasks
- [ ] Rate limiting
- [ ] Authentication
- [ ] Result pagination

## 🧅 Vanity .onion Address

The live API is reachable via a vanity Tor hidden service address generated with [mkp224o](https://github.com/cathugger/mkp224o).

### Generating a vanity address

```bash
git clone https://github.com/cathugger/mkp224o.git
cd mkp224o
./autogen.sh && ./configure && make

# Generate an address starting with "ognon" (CPU-intensive, may take days)
./mkp224o ognon -d output/ -n 1
```

Key options for low-resource machines or long runs:

| Option | Effect |
|--------|--------|
| `-B` | batch mode -- no interactive output, suitable for background |
| `-t 4` | number of threads (match your vCPU count) |
| `-s` | print stats periodically |
| `-n 1` | stop after finding 1 match |
| `-d output/` | write results to a directory instead of stdout |

To run in the background with `screen` so it survives SSH disconnects:

```bash
screen -dmS ognonr ./mkp224o -B -t 4 -s ognon -d output/ -n 1

# Reattach to check progress
screen -r ognonr

# Detach again
# Ctrl-A then D
```

Prefix length directly determines search time -- each additional character multiplies the search space by ~32 (base32 alphabet). A 5-character prefix takes minutes; 7 characters took ~7 days on a single machine.

The tool outputs a directory named after the generated address, containing three files: `hostname`, `hs_ed25519_public_key`, `hs_ed25519_secret_key`.

### Deploying the hidden service (system Tor, Debian/Ubuntu)

```bash
# Copy keys to Tor's data directory
sudo cp -r output/<ADDRESS>.onion /var/lib/tor/ognon-api
sudo chown -R debian-tor:debian-tor /var/lib/tor/ognon-api
sudo chmod 700 /var/lib/tor/ognon-api

# Add to /etc/tor/torrc
echo "HiddenServiceDir /var/lib/tor/ognon-api/" | sudo tee -a /etc/tor/torrc
echo "HiddenServicePort 80 127.0.0.1:<APP_PORT>" | sudo tee -a /etc/tor/torrc

# Tor reads the existing keys instead of generating new ones
sudo systemctl restart tor@default
sudo journalctl -u tor@default -n 20
```

If nginx is your reverse proxy and the .onion address exceeds 64 characters, add this to the `http {}` block in `/etc/nginx/nginx.conf`:

```nginx
server_names_hash_bucket_size 128;
```

## 🌐 Live Instances

| Service | URL |
|---------|-----|
| API | `http://api.dw.13h.be` |
| API (Tor) | `http://ognonapiw2fminc2gfof2rspipqxm3vqwgmrlprtfvab2fpzmy3viuyd.onion` |
| Web client | `http://dw.13h.be` |

The web client at `http://dw.13h.be` is a browser-based UI for walking through the API endpoints, launching search jobs, and reading results in real time.

### Lancer le client web en local

```bash
# Servir les fichiers statiques sur le port 8338
python3 -m http.server 8338 --directory clients/www
```

Ouvrir `http://localhost:8338` dans le navigateur. Le client pointe sur `http://api.dw.13h.be` par défaut.

Pour pointer sur une instance locale (Docker) au lieu de la prod, modifier la première ligne de `clients/www/app.js` :

```js
const API_BASE_URL = "http://localhost:8337";
```

> Ouvrir `index.html` directement en `file://` fonctionne aussi, mais les requêtes CORS vers l'API locale peuvent être bloquées selon le navigateur. Passer par `python3 -m http.server` évite ce problème.

**Vos acces — panneau en haut du client :**

- **Client ID** : generé automatiquement au premier lancement et conservé en `localStorage`. Copiez-le pour retrouver vos jobs depuis un autre appareil ou navigateur.
- **Cle API (plan payant)** : cliquez "Generer une cle API" pour obtenir une cle persistante liée à votre Client ID. Cette cle remplace le Client ID pour toutes les requêtes — utile pour acceder à vos jobs sans dépendre du localStorage. Notez-la : elle n'est pas réaffichée après fermeture.
- **Restaurer une session** : collez une cle API existante dans le champ "Coller une cle existante" et cliquez "Appliquer".
- **Reinitialiser** : supprime le Client ID du localStorage et repart avec un nouvel identifiant.

## 📝 Example Usage

### Using curl

Against the live API:

```bash
# Health check
curl http://api.dw.13h.be/api/v1/health

# Search
curl -X POST http://api.dw.13h.be/api/v1/search -H "Content-Type: application/json" -d '{"term":"cybersec","max_results":5}'

# Poll a job
curl http://api.dw.13h.be/api/v1/jobs/<job_id>

# List jobs
curl http://api.dw.13h.be/api/v1/jobs
```

Or against a local instance:

```bash
# Health check
curl http://localhost:8337/api/v1/health

# Search
curl -X POST http://localhost:8337/api/v1/search -H "Content-Type: application/json" -d '{"term":"cybersec","max_results":5}'

# Poll a job
curl http://localhost:8337/api/v1/jobs/<job_id>

# List jobs
curl http://localhost:8337/api/v1/jobs
```

### Webhooks mini scenario

```bash
# 1) Register the webhook receiver for a client.
# The URL must be HTTPS unless webhook.allow_insecure_urls is enabled for dev.
curl -X PUT http://localhost:8337/api/v1/webhooks/config -H "Content-Type: application/json" -H "X-Client-ID: <CLIENT_ID>" -d '{"url":"https://<WEBHOOK_FQDN>/ognon-radar","events":["job.completed","job.failed"],"secret":"<WEBHOOK_SECRET>","active":true}'

# 2) Check the saved configuration.
curl http://localhost:8337/api/v1/webhooks/config -H "X-Client-ID: <CLIENT_ID>"

# 3) Start a search job for the same client.
# When the job reaches completed or failed, the API POSTs the webhook payload.
curl -X POST http://localhost:8337/api/v1/search -H "Content-Type: application/json" -H "X-Client-ID: <CLIENT_ID>" -d '{"term":"cybersec","max_results":5}'

# 4) Inspect webhook deliveries.
curl "http://localhost:8337/api/v1/webhooks/deliveries?limit=20" -H "X-Client-ID: <CLIENT_ID>"

# 5) Retry failed deliveries manually.
curl -X POST http://localhost:8337/api/v1/webhooks/deliveries/retry -H "X-Client-ID: <CLIENT_ID>"

# 6) Disable the webhook when you no longer need it.
curl -X DELETE http://localhost:8337/api/v1/webhooks/config -H "X-Client-ID: <CLIENT_ID>"
```

Webhook requests include `X-Webhook-Delivery`, `X-Webhook-Attempt`, and,
when a secret is configured, `X-Webhook-Signature: sha256=<HMAC_SHA256>`.

### Using Python

```python
import requests

# Search
response = requests.post(
    "http://localhost:8337/api/v1/search",
    json={
        "term": "cybersec",
        "start_url": "http://your-onion-url.onion",
        "max_results": 10,
        "max_depth": 2
    }
)

results = response.json()
print(f"Found {results['total']} results")
for result in results['results']:
    print(f"- {result['title']}: {result['url']}")
```
