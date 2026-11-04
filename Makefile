# StormIQ developer commands. `make help` lists them.
# Local services come from docker-compose.yml: Postgres on :5433, Redis on :6379.

VENV   ?= .venv
PYTHON ?= python3.13
BIN    := $(VENV)/bin

LOCAL_DB_URL    := postgresql://stormiq:stormiq@localhost:5433/stormiq
TEST_DB_URL     ?= $(LOCAL_DB_URL)
TEST_REDIS_URL  ?= redis://localhost:6379/15
BENCH_REDIS_URL ?= redis://localhost:6379/13

.DEFAULT_GOAL := help
.PHONY: help setup services stack api worker web lint test test-backend test-frontend build check docker bench lock

help: ## List the commands
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Create .venv with the locked dev dependencies and install frontend packages
	test -x $(BIN)/python || $(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -r requirements-dev.txt
	cd frontend && npm ci

services: ## Start local Postgres and Redis in Docker
	docker compose up -d postgres redis

stack: ## Run web, worker, Postgres and Redis in Docker (web on :10000)
	docker compose up --build

api: ## Run the API with reload on :8000
	$(BIN)/uvicorn app:app --reload

worker: ## Run the arq worker
	$(BIN)/arq livestorm_app.worker.WorkerSettings

web: ## Run the Vite dev server on :5173 (proxies /api to :8000)
	cd frontend && npm run dev

lint: ## ruff and ESLint
	$(BIN)/ruff check .
	cd frontend && npm run lint

test: test-backend test-frontend ## pytest and Vitest

test-backend: ## pytest against the local Postgres and Redis (pass flags with ARGS="-k scope")
	TEST_DATABASE_URL=$(TEST_DB_URL) TEST_REDIS_URL=$(TEST_REDIS_URL) $(BIN)/pytest $(ARGS)

test-frontend: ## Vitest
	cd frontend && npm test

build: ## Production frontend build
	cd frontend && npm run build

check: lint test build ## Everything CI runs, except the Docker builds

docker: ## Build the web and worker images
	docker build -t stormiq-web .
	docker build -f docker/Dockerfile.worker -t stormiq-worker .

bench: ## Seed synthetic sessions into the local database and time the heaviest routes
	DATABASE_URL=$(LOCAL_DB_URL) REDIS_URL=$(BENCH_REDIS_URL) $(BIN)/python scripts/seed_benchmark_sessions.py --reset
	DATABASE_URL=$(LOCAL_DB_URL) REDIS_URL=$(BENCH_REDIS_URL) $(BIN)/python scripts/benchmark_routes.py

lock: ## Re-lock Python dependencies after editing requirements*.in
	uv pip compile requirements.in --universal --python-version 3.13 --generate-hashes -o requirements.txt
	uv pip compile requirements-dev.in -c requirements.txt --universal --python-version 3.13 --generate-hashes -o requirements-dev.txt
