# Common development tasks. Run `make help` for the list.

BACKEND := backend
FRONTEND := frontend
VENV := $(BACKEND)/.venv
PY := $(VENV)/bin

.PHONY: help setup infra-up infra-down migrate backend frontend test lint

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

setup: ## Create .env, backend venv and install frontend packages
	test -f .env || cp .env.example .env
	cd $(BACKEND) && uv venv .venv && uv pip install -p .venv -e '.[dev]'
	cd $(FRONTEND) && npm install

infra-up: ## Start Postgres, Redis and MinIO
	docker compose up -d --wait

infra-down: ## Stop the services (data volumes are kept)
	docker compose down

migrate: ## Apply Alembic migrations to the dev database
	cd $(BACKEND) && .venv/bin/alembic upgrade head

backend: ## Run the Flask API on :5000
	cd $(BACKEND) && .venv/bin/flask --app wsgi run --debug

frontend: ## Run the Vite dev server on :5173
	cd $(FRONTEND) && npm run dev

test: ## Run backend and frontend tests
	cd $(BACKEND) && .venv/bin/pytest
	cd $(FRONTEND) && npm test

lint: ## Lint and type-check
	cd $(BACKEND) && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd $(FRONTEND) && npm run typecheck
