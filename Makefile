# =============================================================================
# The Lenny Growth Assistant
#
#   make up        build + start the full stack
#   make ingest    load and index the transcript corpus
#   make test      run the automated test suite
#   make eval      print the measured success metrics
#
# On Windows without `make`, the README lists the equivalent
# `docker compose` commands -- the Makefile is a convenience, not a dependency.
# =============================================================================

SHELL := /bin/sh
COMPOSE := docker compose

.DEFAULT_GOAL := help

.PHONY: help up down restart logs ps build shell-api psql ingest reingest test test-unit test-integration test-xss eval calibrate lint fmt scan-secrets clean reset clean-cache

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- Lifecycle ---------------------------------------------------------------

up: ## Build and start the full stack
	$(COMPOSE) up --build -d
	@echo ""
	@echo "  App:      http://localhost:8080"
	@echo "  API docs: http://localhost:8000/docs"
	@echo ""
	@echo "  First run? Pull models and index the corpus:  make ingest"

down: ## Stop the stack (keeps volumes)
	$(COMPOSE) down

restart: ## Restart the api service only
	$(COMPOSE) restart api

logs: ## Tail logs from all services
	$(COMPOSE) logs -f --tail=100

ps: ## Show service status
	$(COMPOSE) ps

build: ## Rebuild images without starting
	$(COMPOSE) build

# --- Data --------------------------------------------------------------------

ingest: ## Pull models (first run only) then load and index the corpus
	$(COMPOSE) run --rm ollama-init
	$(COMPOSE) run --rm --profile ingest ingest

reingest: ## Force a full re-index, ignoring content hashes
	$(COMPOSE) run --rm --profile ingest ingest --force

# --- Quality -----------------------------------------------------------------

test: test-unit test-integration test-xss ## Run the full automated suite

test-unit: ## Unit tests only (chunking, RRF, sanitizer, router, citations)
	$(COMPOSE) run --rm api pytest tests/unit -v

test-integration: ## Integration tests (API contracts, persistence, sessions)
	$(COMPOSE) run --rm api pytest tests/integration -v

test-xss: ## Hostile HTML artifact payload suite
	$(COMPOSE) run --rm api pytest tests/xss -v

eval: ## Print citation coverage, abstention correctness, and latency percentiles
	$(COMPOSE) run --rm api python -m scripts.eval

calibrate: ## Re-derive RAG_MIN_SCORE from the golden set (writes nothing; see docs/decisions.md)
	$(COMPOSE) run --rm api python -m scripts.calibrate_floor

lint: ## Lint backend and frontend
	$(COMPOSE) run --rm api ruff check app tests
	$(COMPOSE) run --rm api ruff format --check app tests

fmt: ## Auto-format backend
	$(COMPOSE) run --rm api ruff format app tests

scan-secrets: ## Fail if a credential pattern is present in the tree
	$(COMPOSE) run --rm api python -m scripts.scan_secrets

# --- Shells and debugging ----------------------------------------------------

shell-api: ## Open a shell in the api container
	$(COMPOSE) run --rm api sh

psql: ## Open a psql session against the database
	$(COMPOSE) exec db psql -U $${POSTGRES_USER:-lenny} -d $${POSTGRES_DB:-lenny_growth_assistant}

# --- Destructive -------------------------------------------------------------

clean: ## Remove containers and build cache (keeps volumes)
	$(COMPOSE) down --rmi local --remove-orphans

clean-cache: ## Delete the cached transcript corpus
	-$(COMPOSE) exec api rm -rf $${TRANSCRIPT_CACHE_DIR:-/data/transcripts}
	-rm -rf corpus transcripts_cache

reset: ## DESTRUCTIVE: remove containers AND volumes (deletes all data)
	@echo "This deletes the database, model weights, and the corpus cache."
	@printf "Type 'yes' to continue: " && read ans && [ "$$ans" = "yes" ]
	$(COMPOSE) down -v --remove-orphans
