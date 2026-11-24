# Developing Ymer

The architecture and roadmap are in [README.md](README.md). This file covers running the code.

## Layout

```
backend/     Flask 3 API (Python package `ymer`), SQLAlchemy 2, Alembic migrations
collab/      Realtime collaboration service: Hocuspocus (Yjs over WebSockets), Node 22
frontend/    Vite + React 18 + TypeScript + Tailwind CSS 4, TipTap editor
infra/       Init scripts for the Docker Compose services
docker-compose.yml   Postgres 16 + pgvector, Redis 7, MinIO
```

## Prerequisites

Docker (with Compose v2), Python 3.12+, [uv](https://docs.astral.sh/uv/) (or plain `pip`), Node 22.18+
(the collab service runs its TypeScript directly with Node's type stripping).

## First run

```sh
make setup       # .env from .env.example, backend venv, npm install
make infra-up    # Postgres, Redis, MinIO (also creates the `ymer` bucket)
make migrate     # alembic upgrade head
make backend     # API on http://localhost:5000
make collab      # collab service on ws://localhost:1234
make frontend    # app on http://localhost:5173 (proxies /api to :5000, /collab to :1234)
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

## Auth and permissions

- **Schema** (migration `0002`): `users`, `workspaces`, `workspace_members` (role:
  owner / editor / commenter / viewer), `pages` (a tree; type document / database /
  mindmap / folder; a parent must be in the same workspace) and `page_permissions`
  (per-user grants on a page and everything beneath it).
- **Auth** (`ymer/auth/`, routes in `ymer/api/auth.py`): email/password sign-up and
  login with scrypt hashes, then JWT bearer tokens signed with `YMER_SECRET_KEY`. Access
  tokens last `YMER_JWT_ACCESS_TTL` seconds (15 min), refresh tokens
  `YMER_JWT_REFRESH_TTL` (30 days). `POST /api/auth/logout` revokes all of a user's
  tokens by bumping `users.token_version`. In production the app refuses to start
  unless the secret key is at least 32 characters.
- **Scoping** (`ymer/permissions.py`): every workspace route builds a `WorkspaceScope`
  for the signed-in user and reads pages only through it, so queries are always filtered
  by `workspace_id`. Users with no access get 404, never 403, so ids cannot be probed.
- **Effective role on a page**: workspace owners are owners everywhere; otherwise the
  nearest grant on the page or an ancestor wins (it can widen access for a guest or
  narrow it for a member); otherwise the workspace role applies. Viewers read,
  commenters also comment, editors also edit, create, move, delete and share (up to
  their own role). Only owners manage members and the workspace; a workspace always
  keeps one owner; removing a member also removes their page grants there.

Tests: `tests/test_permissions.py` covers the rules above, `tests/test_auth.py` the
tokens, `tests/test_schema.py` the constraints and that the migration matches the
models. The `db` fixture wraps each test in a transaction that is rolled back.

## Collaborative documents (Phase 1)

```
browser ──REST /api──────────▶ Flask ──▶ Postgres (document_content, document_versions,
   │                             ▲                  comments, files) + S3 (file bytes)
   └──WebSocket /collab──▶ collab (Hocuspocus)
                                 └── /internal/collab/* : authorize, fetch, store
```

- **Live editing** (`collab/`): the browser opens a Hocuspocus connection per document,
  named by its page id, with the user's access token. `onAuthenticate` asks Flask
  (`POST /internal/collab/authorize`) whether that user can open the page; viewers and
  commenters get a read-only connection, so their edits are dropped server-side. Flask
  stays the only place that decides permissions and the only writer of the schema.
- **Storage**: Hocuspocus loads and saves the merged Yjs state through Flask
  (`GET/PUT /internal/collab/documents/<page_id>`), debounced (2 s, at most 10 s). These
  routes are outside `/api`, need `X-Ymer-Collab-Secret` = `YMER_COLLAB_SECRET`, and should
  not be routed from the internet by the production reverse proxy.
- **Versions**: a save takes an automatic snapshot when the newest is older than
  `YMER_VERSION_INTERVAL` (10 min). "Save version" sends the editor's live state.
  Restoring replays an old version's content as a new edit, after snapshotting the
  current one, so nothing is lost and everyone connected sees it.
- **Comments**: threads (a first comment plus replies) can be anchored to a text range.
  The anchor is two Yjs relative positions stored on the comment, so it follows edits and
  commenters can anchor without write access. Clients tell each other to refetch with
  Hocuspocus stateless messages (`comments-changed`, `versions-changed`); the collab
  service relays only those.
- **Files**: `POST .../pages/<id>/files` (multipart, editors, `YMER_MAX_UPLOAD_BYTES`,
  25 MB) stores bytes in S3. Documents reference files by id; each viewer gets a 5-minute
  presigned URL after a permission check. Only safe image types and PDF are served inline;
  everything else (including SVG) downloads. Set `YMER_S3_PUBLIC_ENDPOINT_URL` when the
  browser reaches S3 at a different host than the backend does.
- **Editor** (`frontend/src/editor/`): TipTap 3 with StarterKit (headings, lists, quotes,
  code, links), task lists, images and file attachments. `extensions.ts` is the document
  schema; anything outside it is dropped on paste.

Collab tests (`cd collab && npm test`) run the real Hocuspocus server against a stub of
Flask's internal API and connect real providers.
