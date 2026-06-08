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

```bash
# TODO: Add pytest tests
docker-compose run api pytest
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

## 🌐 Live Instances

| Service | URL |
|---------|-----|
| API | `http://api.dw.13h.be` |
| Web client | `http://dw.13h.be` |

The web client at `http://dw.13h.be` is a browser-based UI for walking through the API endpoints, launching search jobs, and reading results in real time.

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
