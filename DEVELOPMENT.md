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

## Notebooks and databases (Phase 2)

Migration `0004` adds the tables below. Everything still hangs off `pages` and is read
through `WorkspaceScope`, so the Phase 0 permission rules apply unchanged: viewers read a
database, editors change its rows, properties and views.

- **Databases** (`ymer/models/database.py`, rules in `ymer/databases.py`, routes in
  `ymer/api/databases.py`). A page of type `database` gets a title property and a table view
  when it is created.
  - `database_properties`: name, type (title, text, number, select, multi_select, date,
    checkbox, url, person, relation, rollup) and a JSONB `config` (select options, number
    format, relation target, rollup settings).
  - `database_rows`: one JSONB `values` object keyed by property id. The server validates
    and normalises each value for its type (URLs must be http, https or mailto; people must
    be workspace members; option ids must exist). Rows have a fractional `position` so a
    drag can drop between two rows without renumbering.
  - `database_relations`: one row per link, stored under the owning relation property. A
    two-way relation adds a second property on the other database whose config has
    `reverse_of`; it reads and writes the same links from the other side. Deleting either
    half deletes both. Relation values are written as row ids and read as `[{id, title}]`;
    `title` is null when the reader cannot see the related database.
  - Rollups (`count`, `count_values`, `count_unique`, `sum`, `average`, `min`, `max`,
    `percent_checked`, `show_original`) are computed on read over one relation. A rollup
    over a database the reader cannot see is empty. Rollups of rollups are refused.
  - `database_views`: table or board, with `config` = `{sorts, filters, group_by, hidden,
    widths}`. `GET .../rows?view_id=` applies the filters (all must match) and sorts; empty
    values sort last. Filtering and sorting run in Python over the loaded rows so computed
    rollups behave like stored values; fine for a few thousand rows, and the place to move
    into SQL if databases grow past that.
  - Changing a property's type converts values where it can (text to select builds options
    from the existing values). Title, relation and rollup properties cannot change type.
- **Backlinks**: type `@` in a document to link a page (`frontend/src/editor/mentions.tsx`,
  stored as a `pageMention` node with the page id). When the collab service saves a
  document, Flask decodes the Yjs state with `pycrdt` (`ymer/content.py`), rebuilds the
  page's rows in `page_links` and stores its plain text in `pages.content_text`.
  `GET .../pages/<id>/backlinks` lists the linking pages the reader can see.
- **Search**: `GET /api/workspaces/<ws>/search?q=` uses generated `tsvector` columns
  (`pages.search_vector` over title and document text, `database_rows.search_vector` over
  title and text values) with the `simple` configuration, every word matched as a prefix,
  plus `pg_trgm` similarity on page titles for typos. Results are limited to pages and
  databases the user can see. Ctrl/Cmd+K opens it in the app.
- **Page tree**: `POST .../pages/<id>/move {parent_id, before_id}` places a page among its
  new siblings and renumbers them; the sidebar uses it for drag and drop (top or bottom
  quarter of a row = before or after it, middle = inside it).
- **Sharing**: `POST .../pages/<id>/permissions {email, role}` shares with an existing
  account by email. The app has a Share dialog on every page and a Members screen
  (`/w/<id>/members`) for owners to add people, change roles and remove them.

Assumptions made for this phase, open to change:

- Database rows hold properties only; a page body per row (as in Notion) is not built yet.
- Databases are not live over Yjs. The app refetches rows every 15 seconds and after each
  change, so two people editing the same table see each other's edits with a short delay.
- Search uses no stemming (`simple`), so it works the same for any language but "plans"
  does not find "plan" unless typed as a prefix.
- Sharing by email needs the person to have an account already; there are no invitations.
- Renumbering siblings on a move also touches pages the mover cannot see. Positions only
  order the tree, so this reveals and changes nothing anyone can read.

## Mind maps, drawings and charts (Phase 3)

Migration `0005` adds a `drawing` page type and a `chart` view type. Mind maps (page type
`mindmap`, which existed since `0002`) and drawings are live pages like documents: one Yjs
document per page, edited through the same collab service, stored in `document_content`
and `document_versions`, with the same permission rules (viewers and commenters get
read-only connections). `LIVE_PAGE_TYPES` in `ymer/models/page.py` lists them; the
internal collab API and version history accept all three, comments stay document-only.

- **Mind maps** (`frontend/src/mindmap/`): React Flow 12 over the Yjs map `mindmap`
  (node id -> map `{label, parent, x, y, color?, pageId?}`; the root's id is always `root`).
  Each node is its own Y.Map, so a rename and a move of the same node by two people both
  survive. `model.ts` holds every change (add child or sibling, delete subtree, re-parent,
  tidy layout, restore). Keyboard: Tab child, Enter sibling, F2 or typing renames, Delete,
  arrow keys move the selection, Ctrl/Cmd+Z undo (only your own changes). Dragging moves a
  subtree; dropping an idea onto another re-parents it. An idea can link to a page, which
  shows up in that page's backlinks. Others' selections show as coloured rings (Yjs
  awareness).
- **Drawings** (`frontend/src/drawing/`): Excalidraw 0.18 over the Yjs map `drawing`
  (element id -> element JSON). Excalidraw versions every element and keeps deleted ones
  as tombstones, so the sync is "newest version wins" per element (`sync.ts`); remote
  changes go through Excalidraw's `reconcileElements`. Live pointers use awareness.
  Excalidraw is loaded lazily, and its fonts are served by the app at
  `/excalidraw-assets/` (a small plugin in `vite.config.ts`) rather than from a CDN.
- **Search and backlinks**: `ymer/content.py` reads mind map labels and linked pages, and
  the text of drawing text elements, on every save.
- **Charts** (`ymer/charts.py`, `frontend/src/charts/`): `POST /api/workspaces/<ws>/databases/<db>/chart`
  takes `{config, view_id?}` and returns labels and series. It groups rows by a property
  (select options in option order and colour, multi-select and people count a row in each
  value, dates bucket by day/week/month/quarter/year, empty values go to "No value"),
  measures a row count or the sum/average/min/max of a number or rollup, can split into
  series by a second property, and can run a cumulative total. Rows are read with
  `RowReader`, so relations and rollups follow the reader's permissions, and a view's
  filters apply when `view_id` is given. Viewers may call it.
  - A database **chart view** stores its config in `config.chart`.
  - A **chart block** in a document (toolbar 📊) is the `databaseChart` node with
    `databaseId`, `viewId` and the config as a JSON string. Readers who cannot see the
    database get a notice instead of the chart.
  - Chart.js draws them, with the categorical palette in `charts/palette.ts` (separate
    light and dark steps) and a "Show data" table under every chart.

Assumptions made for this phase, open to change:

- Excalidraw rather than tldraw: Excalidraw is MIT licensed; tldraw needs a licence key
  in production.
- Drawings have no images yet. Pasted images would need their files synced and stored
  alongside the drawing, so the image tool is turned off.
- Charts read rows live (refetched every 15 seconds like tables). There is no snapshot of
  a chart's data inside the document.
- Comments are for documents only; mind maps and drawings have version history but no
  comment threads.

## AI layer (Phase 4)

Migration `0006` adds `ai_chunks`, `ai_index_queue`, `ai_conversations` and
`ai_messages`. The AI code lives in `backend/ymer/ai/` and runs inside Flask: the browser
never talks to a model or sees a key, and every read goes through `WorkspaceScope`.

- **Providers** (`ai/providers.py`, `ai/fake.py`, `ai/__init__.py`): `build_services`
  picks a chat provider and an embedder from settings and stores them on the app
  (`get_ai()`).
  - `anthropic`: the Anthropic SDK, streamed (`get_final_message()`), with structured
    JSON output for generation, an effort level, and a refusal turned into a 422.
  - `openai`: any OpenAI-compatible `/chat/completions` server, hosted or local.
  - `fake` (the default): an offline stand-in with deterministic output, used by tests
    and by anyone without a key. Every response says `demo: true`, and the UI labels it.
  - `off`: AI endpoints answer 503.
  - Embeddings are separate, because Claude has no embeddings API: `fake` is a
    256-dimension hashing embedder (it matches shared words, not meaning), `openai` calls
    any OpenAI-compatible `/embeddings` endpoint. The `embedding` column has no fixed
    dimension, and each chunk records its model, so switching models only needs
    `flask --app wsgi ai-reindex`.
- **Indexing** (`ai/indexing.py`): `content.outline()` turns a document or mind map into
  Markdown-like text; database rows become "Prop: value" lines. Text is split into chunks
  of about 1,000 characters on paragraph boundaries. A chunk whose hash (model + content)
  already exists is not embedded again. Saves from collab, page create and rename, and
  row and property changes call `mark_dirty`, which adds the page to `ai_index_queue`.
  `flask --app wsgi ai-worker` (`make ai-worker`) drains it with `FOR UPDATE SKIP LOCKED`,
  so several workers can run; a failing page keeps its error and is retried. With
  `YMER_AI_INDEX_INLINE=true` pages are indexed in the request instead (handy in dev).
- **Retrieval** (`ai/retrieval.py`): cosine distance in pgvector, filtered to the pages
  the user can see, at most one hit per page or row, and a distance cut-off so unrelated
  pages are left out.
- **Routes** (`api/ai.py`), all under `/api`:
  - `GET /ai/status`: whether AI is on, the provider and models, and `demo`.
  - `POST /workspaces/<ws>/pages/<id>/ai/assist` `{action, text?, instruction?}`:
    improve, fix, shorten, expand, summarize, bullets or custom. Summarize needs viewer
    access; every other action needs editor.
  - `GET /workspaces/<ws>/ai/search?q=`: semantic search.
  - `POST /workspaces/<ws>/ai/ask` `{question, conversation_id?}`: retrieves with the
    question (plus a few hits for the previous question on a follow-up), answers with
    `[n]` citations and stores both messages. `GET`/`DELETE` on
    `/workspaces/<ws>/ai/conversations[/<id>]` are private to the author, and stored
    sources are filtered again by current access when read.
  - `POST /workspaces/<ws>/ai/generate` `{kind: mindmap|database, source_page_id? | text?,
    parent_id?, title?}`: needs viewer on the source and editor on the parent. A mind map
    is written as a Yjs state with a tidy layout; a database gets properties, select
    options, rows, a table view and a board view when it has a select property.
  - Assist, ask and generate are rate limited per user per minute in Redis
    (`YMER_AI_RATE_LIMIT`, 0 turns it off; the check is skipped if Redis is down).
  - Provider errors answer 502, refusals 422, AI turned off 503.
- **Prompts** (`ai/prompts.py`): page text is wrapped in tags and the system prompts tell
  the model to treat it as data, not instructions. Generation uses JSON schemas
  (`MINDMAP_SCHEMA`, `DATABASE_SCHEMA`), and the server checks sizes and cleans values
  before writing anything.
- **Frontend** (`frontend/src/ai/`): `AssistMenu` (✨ AI in the editor toolbar),
  `GenerateMenu` (✨ Generate in the page header), `AskPage` (`/w/<ws>/ask`) and the
  *Related by meaning* section in the search dialog.

Settings (all `YMER_` prefixed):

| Setting | Default | Meaning |
|---|---|---|
| `AI_PROVIDER` | `fake` | `anthropic`, `openai`, `fake` or `off` |
| `AI_MODEL` | `claude-opus-5-5` | Chat model name sent to the provider |
| `AI_API_KEY` / `AI_BASE_URL` | empty | Key and base URL for the chat provider |
| `AI_EFFORT` | `medium` | Claude effort level |
| `AI_FALLBACKS` | `true` | Retry a declined Claude request on Anthropic's recommended fallback model |
| `AI_TIMEOUT` | `120` | Seconds per model call |
| `AI_EMBEDDING_PROVIDER` | `fake` | `openai`, `fake` or `off` |
| `AI_EMBEDDING_MODEL` | `hash-256` | Embedding model name |
| `AI_EMBEDDING_API_KEY` / `AI_EMBEDDING_BASE_URL` | empty | For the embeddings endpoint |
| `AI_INDEX_INLINE` | `false` (`true` in `.env.example`) | Index in the request instead of the worker |
| `AI_RATE_LIMIT` | `30` | AI requests per user per minute |

Assumptions made for this phase, open to change:

- The default provider is the offline demo model, so a fresh checkout works without a
  key. Real deployments set `YMER_AI_PROVIDER`.
- The default Claude model is `claude-opus-5-5` with medium effort.
- Generation and summaries read the last saved state of a page. The collab service saves
  about 2 seconds after the last keystroke, so a change made a moment earlier may be
  missing.
- Answers are not streamed to the browser yet; the UI shows a waiting state.
- There is no per-workspace AI switch or usage accounting yet.

## Factory, stages F1 to F4: the brief, the architecture, the build and delivery

The Factory turns a workspace's notes into a working app in reviewed stages (brief,
architecture, build, review, deliver). The plan for all stages lives in the project's
Factory plan document; F1 is built.

Migration `0007` adds the `factory` page type and three tables: `factory_projects`
(stage, current brief, approval), `factory_sources` (the pages it reads, in order) and
`factory_questions` (gaps the AI found, with answers). Every stage writes ordinary pages
under the factory page, so people review and edit with the tools they already have: the
brief is a document.

- **Code**: `ymer/factory/` (`brief.py`: prompt, JSON schema, cleaning, the brief's
  layout; `document.py`: writes TipTap documents as Yjs states; `fake.py`: the demo
  model's rule-based brief), routes in `ymer/api/factory.py`, UI in
  `frontend/src/factory/`.
- **Routes**, under `/api/workspaces/<ws>/factories/<factory page id>`:
  - `GET`: stage, sources, questions, the brief page and approval. Viewer access.
  - `PUT /sources` `{page_ids}`: editor; each new source needs viewer access. Folders,
    factories and the factory's own brief cannot be sources. Sources the caller cannot
    open are kept (they could not see them to send them back).
  - `POST /brief`: editor, rate limited like the other AI routes. Reads the sources with
    the caller's access (about 60,000 characters, shared fairly between sources), sends
    answered questions as decisions and dismissed ones as "do not ask", and writes a new
    `Brief` page. An earlier brief is renamed "(earlier draft)" rather than overwritten,
    because it may be open in someone's editor. Open questions are replaced by the new
    ones; answered and dismissed ones stay.
  - `PATCH /questions/<id>` `{answer?, status?: open|dismissed}`: a blank answer reopens.
  - `POST /approve`: editor; needs a brief and no open questions. Saves a named version
    "Approved brief" of the brief and freezes sources, questions and rewrites.
  - `POST /reopen`: takes the approval back.
- **Model**: the same provider as the rest of the AI layer (`YMER_AI_PROVIDER`), with
  structured output (`BRIEF_SCHEMA`). Model output is cleaned and capped before
  anything is written, and the source text is wrapped in tags the model is told to
  treat as data.

Assumptions for F1, open to change:

- Writing the brief runs in the request, like Phase 4's generation. Long-running stages
  (the build) will run on a worker queue.
- The brief reads the last saved state of each source (collab saves about 2 seconds
  after the last edit).
- Only editors approve; there is no separate "approver" role yet.

### Stage F2: the architecture

Migration `0008` adds to `factory_projects` the chosen `stack`, the ids of the
architecture, system map and task pages, and the plan's approval. Stages now run
`sources` → `brief` → `approved` (brief approved) → `architecture` → `planned`.

- **Stacks** (`ymer/factory/stacks.py`): `static-web`, `python-cli` and `flask-react`,
  each with its languages, test command and what gets delivered. Building on a few known
  project shapes is what will make the build stage reliable.
- **Routes**:
  - `POST /architecture` `{stack?}`: editor, rate limited; needs an approved brief.
    Reads the brief page as it is now (people may have edited it), asks the model for an
    architecture (`architecture_schema()`), and writes three pages under the factory:
    *Architecture* (document), *System map* (mind map: app, components, each
    component's tasks) and *Tasks* (database: Task, Status, Component, Priority, Size,
    Depends on, Detail; a table view and a board by Status that hides the long text
    columns). Redesigning renames the earlier pages "(earlier draft)".
  - `POST /approve-plan`: editor; saves named versions "Approved architecture" and
    "Approved system map" and freezes the design.
  - `POST /reopen` now steps back one approval: an approved plan goes back to review,
    otherwise the brief opens again (the architecture pages stay).
- **Cleaning**: the model's components, tasks and dependencies are capped (12
  components, 20 entities, 60 tasks), unknown stacks fall back to `flask-react`, and
  dependencies on components or tasks that do not exist are dropped.
- **Demo model** (`fake_architecture`): picks a stack from words in the brief (terminal
  or CLI → `python-cli`; shared use, votes, accounts, history → `flask-react`;
  otherwise `static-web`), makes a setup task, one task per feature with its priority,
  and an end-to-end test task, and guesses entities from features like "Suggest a place:
  name, cuisine and walking time".

Assumptions for F2, open to change:

- Three stacks to start; which ones matter most is an open question in the plan.
- Task dependencies are stored as text ("Depends on") rather than a relation, so the
  board stays simple; the build stage will read them from there.
- Designing runs in the request like the brief. With Claude it can take a minute.

### Stage F3: the build

Migration `0009` adds `factory_runs` (status, who started it, a snapshot of what it
builds from, steps, tokens, tasks done, summary, error), `factory_run_events` (the log,
numbered per run) and `factory_files` (the app's current files, one row per path). The
stage after a passing build is `built`.

- **Flow** (`ymer/factory/build.py`): `POST /runs` captures the stack, the brief and
  architecture text and the tasks with the starter's access, and queues a run. A worker
  claims it (`FOR UPDATE SKIP LOCKED`, so several workers are safe) and works through
  the tasks in order, skipping Done ones and waiting on unfinished dependencies. Each
  task is a fresh agent conversation that sees the brief and architecture (cached in the
  system prompt), the task list, notes from earlier tasks and the files so far. The task
  moves To do → In progress → Done in the Tasks database. Every step is committed, so
  the page can follow the log while it runs.
- **Agent** (`ymer/factory/agent.py`): tools `list_files`, `read_file`, `write_file`,
  `delete_file`, `run_tests`, `run_command` and `finish_task`. With Claude, it is tool
  use over the Messages API, streamed, with adaptive thinking, `eager_input_streaming`
  on each tool, and every tool input checked against its schema before it runs; a reply
  cut off at `max_tokens` is sent back as an error rather than run. `finish_task` with
  outcome `done` is refused until the tests pass after the last change. Paths must stay
  inside the app (no absolute paths, `..` or `.git`). The OpenAI-compatible provider
  does not do builds yet. The demo model (`fake_agent.py`) writes a placeholder module
  and test per task.
- **Sandbox** (`ymer/factory/sandbox/`): one method, `run(files, command, image,
  timeout)`, stateless. `docker` copies the files into a temporary directory and runs a
  new container per command with `--network none`, `--read-only`, `--cap-drop ALL`,
  `no-new-privileges`, user `nobody`, and memory, CPU, process and time limits. The
  images (`infra/factory/`) carry each stack's test tools, because there is no network
  to install anything. `fake` runs nothing: it parses Python with `ast` and counts tests.
- **Stopping and budgets**: `POST /runs/<id>/stop` stops a queued run at once and a
  running one after its current step. A run fails at the first blocked task, after
  `YMER_FACTORY_TASK_STEPS` steps on one task, or when it reaches
  `YMER_FACTORY_MAX_STEPS` or `YMER_FACTORY_MAX_TOKENS`. A task left In progress goes
  back to To do. Files written so far are kept, so the next build continues.
- **Routes**: `POST /runs` (editor; needs an approved plan, one run at a time), `GET
  /runs`, `GET /runs/<id>?after=<seq>` (the log after a line), `POST /runs/<id>/stop`,
  `GET /files`, `GET /files/content?path=` (viewer). `POST /reopen` on a built factory
  reopens the plan; the files stay.
- **Settings**:

  | Setting | Default | What it does |
  |---|---|---|
  | `YMER_FACTORY_SANDBOX` | `fake` | `docker`, `fake` (runs nothing) or `off` |
  | `YMER_FACTORY_RUN_INLINE` | `false` | Run builds in a thread of the API process (`.env.example` sets `true`) |
  | `YMER_FACTORY_DOCKER_BIN` | `docker` | The Docker command |
  | `YMER_FACTORY_MEMORY` / `YMER_FACTORY_CPUS` | `1g` / `1` | Container limits |
  | `YMER_FACTORY_COMMAND_TIMEOUT` | `300` | Seconds per command |
  | `YMER_FACTORY_MAX_STEPS` | `300` | Model steps per build |
  | `YMER_FACTORY_TASK_STEPS` | `30` | Model steps per task |
  | `YMER_FACTORY_MAX_TOKENS` | `3000000` | Tokens per build (cache reads count a tenth) |

Assumptions for F3, open to change:

- Docker first, hosted later: a hosted sandbox only needs to implement `run`. The
  Docker sandbox needs a Docker daemon next to the worker; giving a worker the Docker
  socket gives it control of that host, so run workers on a machine meant for it.
- The flask-react stack builds and tests the backend; the front end is built when the
  app is delivered (F4), since installing npm packages needs network.
- A worker that dies mid-run leaves that run marked running; there is no automatic
  recovery yet. Pressing Stop twice marks it stopped without waiting for the worker.
- Builds start from the tasks as they were when the build started; edits to the Tasks
  database during a build apply to the next one.

### Stage F4: review and delivery

Migration `0010` adds `factory_releases` (a numbered, frozen copy of the app's files
with notes, who approved it and what changed since the release before), `factory_pushes`
(each push of a release to git: remote without credentials, branch, status, commit or
error) and, on `factory_projects`, the last `git_remote` and `git_branch`. The stage after
the first release is `released`; building again stays possible.

- **Releases** (`ymer/factory/releases.py`): `POST /releases {notes?}` (editor) needs
  the latest build to have passed, no build running, and files that differ from the last
  release. The factory's `delivery` block shows what is unreleased (`added`, `changed`,
  `removed`), which the Review section lists.
- **Packaging** (`ymer/factory/package.py`): a delivered release gets `RELEASE.md` (how to
  run and test it, the notes), a `.gitignore` and, for `python-cli` and `flask-react`, a
  `Dockerfile` (multi-stage for Flask + React). Fixed templates, not model output, and a
  file the build already wrote wins. `GET /releases/<n>/download` returns it as a zip
  under one folder, with stable ordering and timestamps.
- **Preview** (static web apps with an `index.html`): `POST /releases/<n>/preview`
  returns a signed link (`itsdangerous`, `YMER_FACTORY_PREVIEW_MINUTES`) under
  `/api/factory-preview/<token>/...`, which serves the release's files without the login
  header, since an iframe cannot send one. Every response carries
  `Content-Security-Policy: sandbox allow-scripts allow-forms allow-modals` (no
  `allow-same-origin`) and `connect-src 'none'`, so the app runs in an opaque origin even
  when the link is opened in its own tab: it cannot read Ymer's local storage or call the
  API. The link stops working when its creator loses access to the factory.
- **Git** (`ymer/factory/git.py`): `POST /releases/<n>/push {remote, branch, token?}`
  (editor). Only `https://` URLs on `YMER_FACTORY_GIT_HOSTS`, with no credentials in the
  URL; branch names are checked. The token reaches git through `GIT_ASKPASS` in a
  temporary home with system and global config, hooks and credential helpers off, and is
  scrubbed from any error. Ymer fetches the branch (depth 1), replaces its files with the
  release, commits as "Ymer Factory" and pushes without force; if the branch already has
  this release the push is recorded as `unchanged`.
- **Settings**: `YMER_FACTORY_GIT_HOSTS`, `YMER_FACTORY_GIT_ALLOW_LOCAL` (`file://`
  remotes, for tests only), `YMER_FACTORY_GIT_TIMEOUT` (120 s),
  `YMER_FACTORY_PREVIEW_MINUTES` (60).

Assumptions for F4, open to change:

- Ymer writes a Dockerfile but does not build or publish images: that needs network and
  a Docker daemon with registry credentials, which belongs in the team's CI.
- Only static web apps preview in the page. Previewing the Flask + React stack would need
  a running container per preview, which can come with a hosted sandbox.
- A push runs in the request (up to two minutes). Tokens are never stored, so each push
  asks for one; a stored deploy key per factory could come later.
- Releases store a full copy of the files in Postgres (JSONB). That is fine for apps of a
  few hundred files; large apps would move to object storage.

