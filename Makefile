.PHONY: up down build logs test lint clean

# ── Docker ──────────────────────────────────────────────────────────

up:                ## Start API + Tor containers
	docker-compose up -d

up-build:          ## Build and start containers
	docker-compose up -d --build

down:              ## Stop all containers
	docker-compose down

logs:              ## Tail API logs
	docker-compose logs -f api

logs-tor:          ## Tail Tor logs
	docker-compose logs -f tor

restart:           ## Restart API container
	docker-compose restart api

ps:                ## Show running containers
	docker-compose ps

shell:             ## Open a shell inside the running API container
	docker-compose exec api /bin/bash

# ── Tests ───────────────────────────────────────────────────────────

test:              ## Run tests inside Docker (this project has NO local venv)
	docker-compose run --rm api python -m pytest tests/ -v

# ── Profiles ────────────────────────────────────────────────────────

crawler:           ## Start crawler profile
	docker-compose --profile crawler up

locust:            ## Start load testing UI (http://localhost:8089)
	docker-compose --profile testing up -d

# ── Cleanup ─────────────────────────────────────────────────────────

clean:             ## Remove containers, volumes, and __pycache__
	docker-compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true

# ── Help ────────────────────────────────────────────────────────────

help:              ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
