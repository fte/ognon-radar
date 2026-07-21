# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Behavioral Guidelines

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

### 5. Testing Rules

**Never break a test without fixing it in the same change.**

- Run `docker-compose run api pytest` before and after any non-trivial change.
- If your change breaks a test: fix the code OR fix the test — never delete or skip the test to make CI pass.
- If a test is wrong (testing the wrong thing), explain why before touching it.
- New behaviour must have a corresponding test. No untested public-facing logic.

### 6. Mandatory Anonymization In Shared Artifacts

When producing documentation, checklists, examples, or chat-ready snippets:
- Always anonymize usernames, hostnames, domains/FQDNs, IPs, and absolute paths.
- Use placeholders like `<DEPLOY_USER>`, `<VPS_HOST>`, `<API_FQDN>`, `<APP_DIR>`.
- Do not commit real infrastructure identifiers in Markdown files.
- Keep real values in secrets/local environment only.

---

## What This Is

A Docker-only REST API (FastAPI) for crawling and searching Tor `.onion` hidden services. All traffic routes through a Tor SOCKS5 proxy container. Intended for authorized security research only.

## Running the API

```bash
# Build and start API + Tor containers (with hot reload)
docker-compose up --build

# Background
docker-compose up -d

# Logs
docker-compose logs -f api
docker-compose logs -f tor

# Rebuild after code changes
docker-compose up --build

# Run the standalone CLI crawler (separate profile)
docker-compose --profile crawler run --rm crawler python dark_crawler.py -u http://xxx.onion --json

# Load testing UI at http://localhost:8089
docker-compose --profile testing up
```

The API is at `http://localhost:8337`. Swagger at `/docs`, ReDoc at `/redoc`.

## Verifying Config YAML

```bash
python -c "import yaml; yaml.safe_load(open('config.yaml'))"
```

## Architecture

**Docker networking**: The Tor proxy runs as service `tor` in `docker-compose.yml`. The API connects to it via `socks5h://tor:9050` — never `localhost`. The API container waits for the Tor container to pass its healthcheck before starting.

**Request flow**: `POST /api/v1/search` → `routes/search.py` → creates `OnionCrawler(tor_client)` → `core/crawler.py` runs BFS over `.onion` URLs using `core/tor_client.py` for all HTTP requests.

**Configuration**: All settings live in `config.yaml` (mounted read-only into the container). `config.py` loads it into a `Settings` singleton (`settings`) at import time. Never hardcode values — always read from `settings`.

**Tor client** (`core/tor_client.py`): A `TorClient` singleton (`tor_client`) wraps a `requests.Session` configured with the SOCKS5 proxy. Exposes `get_with_retries()` with exponential backoff (`backoff_factor * 2^(attempt-1)`). The session is initialized in FastAPI's `lifespan` handler in `main.py`.

**Crawler** (`core/crawler.py`): `OnionCrawler.crawl_and_search()` implements BFS. It validates URLs against the Tor v3 pattern (56 base32 chars), skips blacklisted paths (login/register), enforces `settings.crawl_delay` (default 7s) between requests, and returns `(results_list, crawled_page_count)`.

**Schemas** (`models/schemas.py`): Pydantic v2 models. `SearchRequest` validates `start_url` as a Tor v3 address. If `start_url` is omitted, the endpoint falls back to `settings.seed_urls[0]`.

## Key Constraints

- **Docker-only**: No local Python environment is used for running the service. All execution happens inside containers.
  - **Do NOT** run `pip install`, `.venv/bin/...`, or any local Python commands.
  - **Do NOT** assume a local `.venv` exists — it doesn't.
  - **Use `make test`** (runs `docker-compose run --rm api pytest`) or `docker-compose run --rm api python ...` directly.
- **No localhost between services**: Use Docker service names (`tor`, `api`) for inter-container communication.
- **Async route handlers**: Route functions use `async def`, but `OnionCrawler.crawl_and_search()` is synchronous (it calls `time.sleep()`). This blocks the event loop during crawls — a known limitation noted in the roadmap (Celery background tasks).
- **Config changes**: Edit `config.yaml`, then `docker-compose restart api` (no rebuild needed since it's volume-mounted).
