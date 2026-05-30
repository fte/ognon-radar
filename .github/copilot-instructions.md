# ognon-radar - Copilot Instructions

## Project Overview

This is a **REST API** for crawling and searching .onion (Tor hidden services) sites. The project is inspired by the DarkCrawler tool but redesigned as an API-first architecture.

**Primary Goal**: Provide RESTful endpoints to search for .onion sites containing specific keywords and capture screenshots.

## Security Redaction Rule (Mandatory)

When generating documentation, checklists, or example commands intended for sharing:
- Always anonymize users, paths, hostnames, domains/FQDNs, and IP addresses.
- Use placeholders (for example: `<DEPLOY_USER>`, `<APP_DIR>`, `<VPS_HOST>`, `<API_FQDN>`).
- Do not include real infrastructure identifiers in tracked Markdown docs.

## Architecture Principles

### 1. REST API Best Practices
- **Use proper HTTP methods**: GET for retrieval, POST for creation, DELETE for removal
- **Resource-based URLs**: `/api/v1/search`, `/api/v1/screenshots/{id}`
- **Status codes**: 200 (success), 201 (created), 400 (bad request), 404 (not found), 500 (server error)
- **JSON responses**: All responses should be JSON formatted
- **Pagination**: Use `limit` and `offset` for large result sets
- **Versioning**: All endpoints use `/api/v1/` prefix

### 2. Docker-Only Environment
- **NO local installation**: Everything runs in Docker containers
- **Use docker-compose.yml**: Define all services (API, Tor, Redis if needed)
- **YAML configuration**: All settings in `config.yaml` (no environment variables)
- **Tor proxy**: Run Tor in a separate container, API connects via SOCKS5 proxy

### 3. Code Organization
```
ognon-radar/
├── main.py                 # FastAPI application entry point
├── core/                   # Business logic
│   ├── tor_client.py      # Tor SOCKS5 proxy client
│   ├── crawler.py         # .onion crawling logic
│   └── search.py          # Search and filtering
├── models/                # Pydantic schemas
│   └── schemas.py         # Request/response models
├── routes/                # API endpoints
│   ├── search.py          # Search endpoints
│   └── health.py          # Health check
├── config.py              # Configuration management
├── requirements.txt       # Python dependencies
├── Dockerfile             # API container
└── docker-compose.yml     # Multi-container setup
```

## Technical Stack

### Core Technologies
- **FastAPI**: Modern Python web framework for APIs
- **Uvicorn**: ASGI server
- **Pydantic**: Data validation for schemas
- **PyYAML**: YAML configuration management
- **requests[socks]**: HTTP client with SOCKS5 support for Tor
- **BeautifulSoup4**: HTML parsing
- **lxml**: XML/HTML parser

### Tor Integration
- **Tor SOCKS5 Proxy**: All requests route through `socks5h://tor:9050`
- **Anonymous crawling**: No direct connections to .onion sites
- **Circuit renewal**: Periodic identity refresh for anonymity

## API Endpoints

### Phase 1: Search Functionality

#### POST /api/v1/search
Search for .onion sites containing a specific term.

**Request Body:**
```json
{
  "term": "cybersec",
  "max_results": 10,
  "timeout": 30,
  "depth": 2
}
```

**Response:**
```json
{
  "term": "cybersec",
  "results": [
    {
      "url": "http://xxx.onion",
      "title": "CyberSec Research Hub",
      "snippet": "...cybersec resources and tools...",
      "timestamp": "2025-12-12T10:30:00Z",
      "depth": 1
    }
  ],
  "total": 5,
  "crawled_pages": 25
}
```

#### GET /api/v1/health
Health check endpoint.

**Response:**
```json
{
  "status": "ok",
  "tor_connected": true,
  "timestamp": "2025-12-12T10:30:00Z"
}
```

### Phase 2: Screenshot Functionality (Future)

#### POST /api/v1/screenshots
Capture screenshot of a .onion site.

**Request Body:**
```json
{
  "url": "http://xxx.onion",
  "highlight_term": "cybersec"
}
```

## Security & Ethics

### Important Considerations
- **Authorized use only**: This tool is for security research and authorized testing
- **Rate limiting**: Implement delays between requests (7+ seconds)
- **Respect robots.txt**: Honor site crawling policies
- **No illegal content**: Never access or store illegal material
- **Tor anonymity**: Always route through Tor, never direct connections

### Legal Compliance
- Obtain proper authorization before crawling any sites
- Follow local, national, and international laws
- Document all activities for compliance
- Report discovered vulnerabilities responsibly

## Development Guidelines

### When Writing Code
1. **Always use type hints**: `def search(term: str) -> List[SearchResult]`
2. **Async when possible**: Use `async def` for I/O operations
3. **Error handling**: Try/except blocks with proper logging
4. **Logging**: Use Python logging module, not print statements
5. **Configuration**: Never hardcode values, load from `config.yaml`
6. **Docker networking**: Use service names (e.g., `tor:9050`, not `localhost:9050`)

### Code Style
- Follow PEP 8 standards
- Use descriptive variable names
- Add docstrings to all functions
- Keep functions small and focused
- Avoid global state

### Testing
- Test all endpoints with curl or Postman
- Verify Tor connectivity before crawling
- Test error scenarios (timeout, invalid URL, etc.)
- Use `docker-compose up` to test full stack

## Common Patterns

### Tor Session Creation
```python
import requests

def create_tor_session() -> requests.Session:
    session = requests.Session()
    session.proxies = {
        'http': 'socks5h://tor:9050',
        'https': 'socks5h://tor:9050'
    }
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    return session
```

### Error Response
```python
from fastapi import HTTPException

raise HTTPException(
    status_code=400,
    detail="Invalid .onion URL format"
)
```

### Configuration
```python
import yaml

class Settings:
    def __init__(self, config_path: str = "config.yaml"):
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        self.tor_proxy = config['tor']['proxy']
        self.crawl_delay = config['crawling']['delay']
        self.max_depth = config['crawling']['max_depth']

settings = Settings()
```

**config.yaml:**
```yaml
tor:
  proxy: "socks5h://tor:9050"

crawling:
  delay: 7
  max_depth: 2
  max_pages: 50
```

## Docker Commands

### Build and Run
```bash
# Start all services
docker-compose up --build

# Start in background
docker-compose up -d

# Start with load testing
docker-compose --profile testing up
```

### View Logs
```bash
docker-compose logs -f api
docker-compose logs -f tor
```

### Configuration Changes
```bash
# Edit config.yaml, then restart
nano config.yaml
## Troubleshooting

### Tor Connection Issues
- Check if Tor container is running: `docker-compose ps`
- View Tor logs: `docker-compose logs tor`
- Verify proxy settings in `config.yaml`: should use `tor:9050` (service name), not `localhost`

### API Not Responding
- Check API logs: `docker-compose logs api`
- Verify port mapping in docker-compose.yml
- Ensure config.yaml is mounted correctly
- Check if FastAPI is binding to `0.0.0.0` not `127.0.0.1`

### Configuration Issues
- Verify YAML syntax: `python -c "import yaml; yaml.safe_load(open('config.yaml'))"`
- Check file is mounted: `docker-compose exec api cat /app/config.yaml`
- Restart after changes: `docker-compose restart api`

### Slow Response Times
- .onion sites are inherently slow
- Increase timeout values in config.yaml
- Reduce crawl depth and max_pages
- Implement caching (Redis)ould use `tor:9050` (service name), not `localhost`

### API Not Responding
- Check API logs: `docker-compose logs api`
- Verify port mapping in docker-compose.yml
- Check if FastAPI is binding to `0.0.0.0` not `127.0.0.1`

### Slow Response Times
- .onion sites are inherently slow
- Increase timeout values
- Reduce crawl depth
- Implement caching (Redis)

## Performance Optimization

- **Caching**: Cache search results in Redis
- **Async operations**: Use aiohttp instead of requests
- **Background tasks**: Use Celery for long-running crawls
- **Rate limiting**: Implement token bucket algorithm
- **Connection pooling**: Reuse Tor session connections

## Monitoring

- Log all API requests with timestamps
- Track crawl success/failure rates
- Monitor Tor circuit health
- Alert on repeated failures
- Record response times

---

**Remember**: This is a Docker-only environment. All code must work within containers. Never assume local file system access or localhost connectivity between services.