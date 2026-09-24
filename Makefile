# ── Container backend (docker-compose vs Apple Container) ────────────────
# This project can run on two container backends:
#   - Docker (Docker Desktop or Colima)      → docker-compose
#   - Apple Container (macOS 26+, CLI `container`) → scripts/container/*.sh
#
# Selection — variable RUNTIME, overridable on the command line:
#   RUNTIME=auto       (default) auto-detect; the Apple `container` CLI wins
#                      when installed, otherwise docker is used.
#   RUNTIME=docker     force docker-compose
#   RUNTIME=container  force scripts/container/*.sh
#
# Examples:
#   make up                     # auto-detect
#   make up RUNTIME=docker      # force the docker backend
#   make runtime                # show what was detected

RUNTIME ?= auto

# Prefer the deployment virtualenv when present (on-premise), otherwise use
# the host interpreter used by local development or CI.
OPENAPI_PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

# Detect installed CLIs (cheap `command -v` checks, run once at parse time).
DOCKER_CLI    := $(shell command -v docker >/dev/null 2>&1 && echo yes || echo no)
CONTAINER_CLI := $(shell command -v container >/dev/null 2>&1 && echo yes || echo no)
# Compose runner for the docker backend: the standalone `docker-compose`
# binary if present, else the v2 plugin (`docker compose`). Empty if neither
# exists — the guard below reports it instead of a raw "command not found".
DOCKER_COMPOSE := $(shell if command -v docker-compose >/dev/null 2>&1; then echo docker-compose; elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then echo 'docker compose'; fi)

ifneq ($(filter $(RUNTIME),auto docker container),$(RUNTIME))
  $(error RUNTIME must be one of: auto | docker | container (got "$(RUNTIME)"))
endif

ifeq ($(RUNTIME),docker)
  RUNTIME_EFFECTIVE := docker
else ifeq ($(RUNTIME),container)
  RUNTIME_EFFECTIVE := container
else ifeq ($(CONTAINER_CLI),yes)
  # Apple `container` installed → native macOS VM container server.
  RUNTIME_EFFECTIVE := container
else ifeq ($(DOCKER_CLI),yes)
  # Docker CLI present — the daemon may be Docker Desktop or Colima.
  RUNTIME_EFFECTIVE := docker
else
  RUNTIME_EFFECTIVE := none
endif

# ── Commands per runtime ─────────────────────────────────────────────────
ifeq ($(RUNTIME_EFFECTIVE),container)
  UP_CMD       := scripts/container/up.sh
  UP_BUILD_CMD := scripts/container/up.sh
  DOWN_CMD     := scripts/container/down.sh
  LOGS_CMD     := scripts/container/logs.sh api
  LOGS_TOR_CMD := scripts/container/logs.sh tor
  RESTART_CMD  := scripts/container/restart.sh
  PS_CMD       := scripts/container/ps.sh
  SHELL_CMD    := scripts/container/shell.sh
  TEST_CMD     := scripts/container/test.sh
  CRAWLER_CMD  := scripts/container/crawler.sh
  LOCUST_CMD   := scripts/container/locust.sh
  CLEAN_CMD    := scripts/container/down.sh --clean
else
  UP_CMD       := $(DOCKER_COMPOSE) up -d
  UP_BUILD_CMD := $(DOCKER_COMPOSE) up -d --build
  DOWN_CMD     := $(DOCKER_COMPOSE) down
  LOGS_CMD     := $(DOCKER_COMPOSE) logs -f api
  LOGS_TOR_CMD := $(DOCKER_COMPOSE) logs -f tor
  RESTART_CMD  := $(DOCKER_COMPOSE) restart api
  PS_CMD       := $(DOCKER_COMPOSE) ps
  SHELL_CMD    := $(DOCKER_COMPOSE) exec api /bin/bash
  TEST_CMD     := $(DOCKER_COMPOSE) run --rm api python -m pytest tests/ -v
  CRAWLER_CMD  := $(DOCKER_COMPOSE) --profile crawler up
  LOCUST_CMD   := $(DOCKER_COMPOSE) --profile testing up -d
  CLEAN_CMD    := $(DOCKER_COMPOSE) down -v
endif

.PHONY: up up-build down logs logs-tor restart ps shell test crawler locust clean runtime openapi openapi-check help _guard-runtime

# Fail fast with guidance when no container backend (or compose runner) is available.
_guard-runtime:
	@if [ "$(RUNTIME_EFFECTIVE)" = "none" ]; then \
		echo "No container backend detected on this machine."; \
		echo "  Install Docker (Docker Desktop / Colima) or the Apple 'container' CLI"; \
		echo "  (macOS 26+, https://github.com/apple/container/releases)."; \
		exit 1; \
	fi
	@if [ "$(RUNTIME_EFFECTIVE)" = "docker" ] && [ -z "$(DOCKER_COMPOSE)" ]; then \
		echo "Docker CLI found, but no compose runner (docker-compose or the v2 'docker compose' plugin)."; \
		echo "  Install it with: brew install docker-compose   (or enable the Docker Compose plugin)."; \
		exit 1; \
	fi

up: _guard-runtime          ## Start API + Tor containers (extra args via ARGS="...")
	$(UP_CMD) $(ARGS)

up-build: _guard-runtime    ## Build and start containers
	$(UP_BUILD_CMD)

down: _guard-runtime        ## Stop all containers
	$(DOWN_CMD)

logs: _guard-runtime        ## Tail API logs
	$(LOGS_CMD)

logs-tor: _guard-runtime    ## Tail Tor logs
	$(LOGS_TOR_CMD)

restart: _guard-runtime     ## Restart API container
	$(RESTART_CMD)

ps: _guard-runtime          ## Show running containers
	$(PS_CMD)

shell: _guard-runtime       ## Open a shell inside the running API container
	$(SHELL_CMD)

test: _guard-runtime        ## Run tests (extra pytest args via ARGS="...")
	$(TEST_CMD) $(ARGS)

crawler: _guard-runtime     ## Start crawler profile (args via ARGS="...")
	$(CRAWLER_CMD) $(ARGS)

locust: _guard-runtime      ## Start load testing UI (http://localhost:8089)
	$(LOCUST_CMD)

clean: _guard-runtime       ## Remove containers, volumes, and __pycache__
	$(CLEAN_CMD)
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true

runtime:                    ## Show the detected container backend
	@echo "RUNTIME override : $(RUNTIME)"
	@echo "Effective runtime: $(RUNTIME_EFFECTIVE)"
	@if [ "$(RUNTIME_EFFECTIVE)" = "container" ]; then \
		container --version 2>/dev/null || true; \
		container system status 2>/dev/null | head -3 || true; \
	elif [ "$(RUNTIME_EFFECTIVE)" = "docker" ]; then \
		docker --version 2>/dev/null || true; \
		echo "Current context: $$(docker context show 2>/dev/null)"; \
	fi

openapi:                    ## Regenerate openapi.json + openapi.yaml from the FastAPI app
	@if $(OPENAPI_PYTHON) -c 'import fastapi, yaml' >/dev/null 2>&1; then \
		$(OPENAPI_PYTHON) scripts/gen_openapi.py; \
	else \
		echo "FastAPI and PyYAML are not installed for $(OPENAPI_PYTHON)."; \
		echo "Install requirements in the active environment, then rerun make openapi."; \
		exit 1; \
	fi

openapi-check:              ## Verify openapi.json + openapi.yaml match the app (also runs in CI)
	@if $(OPENAPI_PYTHON) -c 'import fastapi, yaml' >/dev/null 2>&1; then \
		$(OPENAPI_PYTHON) scripts/gen_openapi.py --check; \
	else \
		echo "FastAPI and PyYAML are not installed for $(OPENAPI_PYTHON)."; \
		echo "Install requirements in the active environment, then rerun make openapi-check."; \
		exit 1; \
	fi

help:                       ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
