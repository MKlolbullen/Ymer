# Common development tasks. Run `make help` for the list.

BACKEND := backend
FRONTEND := frontend
COLLAB := collab
VENV := $(BACKEND)/.venv
PY := $(VENV)/bin

.PHONY: help setup infra-up infra-down migrate backend collab frontend ai-worker factory-worker factory-images test lint

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

setup: ## Create .env, backend venv and install collab and frontend packages
	test -f .env || cp .env.example .env
	cd $(BACKEND) && uv venv .venv && uv pip install -p .venv -e '.[dev]'
	cd $(COLLAB) && npm install
	cd $(FRONTEND) && npm install

infra-up: ## Start Postgres, Redis and MinIO
	docker compose up -d --wait

infra-down: ## Stop the services (data volumes are kept)
	docker compose down

migrate: ## Apply Alembic migrations to the dev database
	cd $(BACKEND) && .venv/bin/alembic upgrade head

backend: ## Run the Flask API on :5000
	cd $(BACKEND) && .venv/bin/flask --app wsgi run --debug

collab: ## Run the realtime collab service (Hocuspocus) on :1234
	cd $(COLLAB) && npm run dev

frontend: ## Run the Vite dev server on :5173
	cd $(FRONTEND) && npm run dev

ai-worker: ## Embed changed pages for semantic search and Q&A (not needed with YMER_AI_INDEX_INLINE=true)
	cd $(BACKEND) && .venv/bin/flask --app wsgi ai-worker

factory-worker: ## Run Factory builds (not needed with YMER_FACTORY_RUN_INLINE=true)
	cd $(BACKEND) && .venv/bin/flask --app wsgi factory-worker

factory-images: ## Build the Docker images Factory builds run in
	for stack in static-web python-cli flask-react node-cli python-api; do \
		docker build -t ymer-factory/$$stack:1 -f infra/factory/$$stack.Dockerfile infra/factory; \
	done

test: ## Run backend, collab and frontend tests
	cd $(BACKEND) && .venv/bin/pytest
	cd $(COLLAB) && npm test
	cd $(FRONTEND) && npm test

lint: ## Lint and type-check
	cd $(BACKEND) && .venv/bin/ruff check . && .venv/bin/ruff format --check .
	cd $(COLLAB) && npm run typecheck
	cd $(FRONTEND) && npm run typecheck
