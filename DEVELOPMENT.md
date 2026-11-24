# Developing Ymer

The architecture and roadmap are in [README.md](README.md). This file covers running the code.

## Layout

```
backend/     Flask 3 API (Python package `ymer`), SQLAlchemy 2, Alembic migrations
frontend/    Vite + React 18 + TypeScript + Tailwind CSS 4
infra/       Init scripts for the Docker Compose services
docker-compose.yml   Postgres 16 + pgvector, Redis 7, MinIO
```

## Prerequisites

Docker (with Compose v2), Python 3.12+, [uv](https://docs.astral.sh/uv/) (or plain `pip`), Node 22+.

## First run

```sh
make setup       # .env from .env.example, backend venv, npm install
make infra-up    # Postgres, Redis, MinIO (also creates the `ymer` bucket)
make migrate     # alembic upgrade head
make backend     # API on http://localhost:5000
make frontend    # app on http://localhost:5173 (proxies /api to :5000)
```

Without `make` or `uv`: `python -m venv backend/.venv && backend/.venv/bin/pip install -e 'backend[dev]'`.

Check everything is wired up: `curl localhost:5000/api/health/ready` should report
`database`, `redis` and `storage` as `ok`. The sidebar of the app shows a green
"API 0.1.0" dot when the frontend can reach the backend.

## Services

| Service  | URL / port                         | Credentials (from `.env`)       |
|----------|------------------------------------|---------------------------------|
| Postgres | `localhost:5432`, dbs `ymer` and `ymer_test` | `ymer` / `ymer`        |
| Redis    | `localhost:6379`                   | none                            |
| MinIO    | API `localhost:9000`, console http://localhost:9001 | `ymer` / `ymer-dev-secret` |

Postgres has the `vector` (pgvector) and `pg_trgm` extensions enabled. The init
script in `infra/postgres/init/` only runs when the volume is first created; to
start from scratch, run `docker compose down -v` (this deletes all local data).

**MinIO image:** the official `minio/minio` image is no longer published on Docker
Hub, so Compose uses `pgsty/minio`, a community build of MinIO from source with the
same CLI and S3 API, pinned to a release tag. Set `MINIO_IMAGE` in `.env` to use a
different build. `flask --app wsgi init-storage` creates the bucket from the backend
if you point it at storage that Compose did not set up.

## Backend

- App factory: `ymer/app.py` (`create_app(overrides=None)`); WSGI entry: `backend/wsgi.py`.
- Settings: `ymer/config.py`, a pydantic-settings class reading `YMER_*` env vars
  and `.env` (repo root, then `backend/`). `YMER_ENV=test` makes the app use
  `YMER_TEST_DATABASE_URL`.
- Database: `ymer/db.py` holds the declarative `Base` (with a constraint naming
  convention), the engine, and `db_session`, a request-scoped session removed on
  app-context teardown.
- Models go in `ymer/models/`; `ymer/models/__init__.py` must import every model
  module so Alembic autogenerate sees them.
- API: blueprints under `ymer/api/`, mounted at `/api`. Errors are returned as JSON.
- Health: `GET /api/health` (liveness, no dependencies) and `GET /api/health/ready`
  (checks Postgres, Redis and S3; 503 when any is down).

Migrations (run from `backend/`):

```sh
.venv/bin/alembic revision --autogenerate -m "add users"
.venv/bin/alembic upgrade head
.venv/bin/alembic -x db_url=postgresql+psycopg://ymer:ymer@localhost:5432/ymer_test upgrade head
```

Tests use pytest. Tests marked `@pytest.mark.db` need Postgres at
`YMER_TEST_DATABASE_URL` and are skipped when it is not reachable.

## Frontend

- `src/components/AppShell.tsx`: sidebar, header and content area.
- `src/lib/api.ts`: fetch wrapper; requests go to `/api`, which Vite proxies to
  `VITE_API_PROXY_TARGET` (default `http://localhost:5000`).
- Tailwind 4 is configured in CSS (`src/index.css`, `@theme` block), not in a JS config.
- `@/` is an alias for `src/`.

```sh
npm run dev | build | typecheck | test
```
