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

## 📝 Example Usage

### Using curl

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

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## ⚠️ Disclaimer

This tool is provided for **educational purposes and authorized security research only**. The developers assume NO LIABILITY for misuse. Users are SOLELY RESPONSIBLE for:

- Ensuring proper authorization for all activities
- Complying with all applicable laws
- Protecting operational security
- Ethical handling of collected information

By using this tool, you acknowledge full responsibility for your actions.

## 📄 License

MIT License - See LICENSE file for details

## 📧 Contact

For security issues or questions:
- Create a GitHub issue
- Email: [your-email]

---

**ognon-radar** | For Authorized Security Research Only
