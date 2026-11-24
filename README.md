# Ymer — multi-user creator platform combining collaborative documents, Notion-style notebooks/databases, mind mapping, note-taking, charts/visuals, and general file handling.

> **Running the code:** see [DEVELOPMENT.md](DEVELOPMENT.md) for setup, services and commands.

### 1. High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     React + Tailwind                    │
│  (TipTap/Slate, React Flow, Chart.js/D3, Yjs clients)  │
└───────────────────────┬─────────────────────────────────┘
                        │ REST + WebSocket
┌───────────────────────▼─────────────────────────────────┐
│                    Flask API Gateway                    │
│         (Auth, Permissions, Rate limiting, Routing)     │
└───────┬─────────────────────┬───────────────────────────┘
        │                     │
┌───────▼────────┐   ┌────────▼────────┐   ┌──────────────┐
│  Core Services │   │  Realtime Collab│   │   AI Service │
│  (Documents,   │   │  (Yjs + SocketIO│   │  (LLM proxy, │
│   Databases,   │   │   or separate   │   │   embeddings)│
│   Mind maps)   │   │   collab server)│   │              │
└───────┬────────┘   └────────┬────────┘   └──────┬───────┘
        │                     │                   │
┌───────▼─────────────────────▼───────────────────▼───────┐
│              PostgreSQL + Redis + Object Storage        │
│         (S3-compatible: MinIO / Cloudflare R2 / S3)     │
└─────────────────────────────────────────────────────────┘
```

- **Frontend**: React (Vite) + Tailwind CSS + TypeScript.  
- **Backend**: Flask (or FastAPI if you later want async-first; start with Flask as requested).  
- **Real-time**: Yjs (CRDT) + WebSocket layer (Flask-SocketIO or a small dedicated collab service).  
- **Database**: PostgreSQL (primary) + Redis (sessions, presence, rate limits, queues).  
- **Storage**: S3-compatible for images, attachments, exports.  
- **AI**: Pluggable LLM layer (OpenAI, Anthropic, local Ollama/vLLM, or Grok). Embeddings in pgvector.

### 2. Core Feature Modules

| Module              | Key Capabilities                                      | Primary Libraries / Approach                  |
|---------------------|-------------------------------------------------------|-----------------------------------------------|
| **Auth & Workspaces** | Multi-user, teams, roles (Owner/Editor/Commenter/Viewer), sharing links, SSO later | Flask-Login / JWT + Casbin or custom RBAC    |
| **Documents**       | Rich text, collaborative editing, comments, versions  | TipTap (ProseMirror) + Yjs                   |
| **Notebooks / Collections** | Hierarchical pages, nested notes, backlinks         | Custom tree + full-text search (pg_trgm or Meilisearch) |
| **Databases**       | Notion-style tables, properties (text, select, date, relation, formula), views (table/board/gallery/calendar) | Custom schema + JSONB + generated columns    |
| **Mind Maps**       | Infinite canvas, nodes, edges, grouping, export      | React Flow + Yjs                              |
| **Visuals & Charts**| Embedded charts, diagrams, image annotation, Excalidraw-style drawings | Chart.js / Recharts / D3 + Excalidraw or tldraw |
| **AI Layer**        | Summarize, expand, rewrite, generate mind maps from notes, auto-tag, Q&A over workspace, image descriptions | LLM proxy + RAG (pgvector) + background jobs |
| **Files & Media**   | Upload, preview, embed, versioning                    | Presigned S3 + thumbnail service              |

### 3. Suggested Data Model (PostgreSQL)

Core tables (simplified):

- `users`, `workspaces`, `workspace_members` (roles)
- `pages` (polymorphic: document | database | mindmap | folder)
- `page_permissions`
- `blocks` or `document_content` (for TipTap/Yjs snapshots + operational log)
- `database_properties`, `database_rows` (JSONB for flexible properties)
- `relations` (many-to-many between rows/pages)
- `mindmap_nodes` / `mindmap_edges` (or store as Yjs doc)
- `files` (metadata + S3 key)
- `ai_conversations` / `embeddings` (pgvector)
- `activity_log` / `versions`

Use JSONB heavily for flexibility (Notion-style properties) while keeping relational integrity for permissions and structure.

### 4. Development Roadmap

**Phase 0 – Foundation (2–3 weeks)**  
- Project scaffolding (Flask + React monorepo or separate repos)  
- Auth (email/password + JWT, basic workspaces)  
- PostgreSQL schema + migrations (Alembic)  
- Basic page CRUD + permissions  
- Tailwind design system + layout shell

**Phase 1 – Collaborative Documents (3–4 weeks)**  
- TipTap editor with basic blocks  
- Yjs + WebSocket collaboration (presence, cursors)  
- Comments + version history  
- File uploads

**Phase 2 – Notebooks + Databases (4–5 weeks)**  
- Hierarchical pages & sidebar  
- Database engine (properties, rows, basic views: table + board)  
- Relations & rollups (simplified)  
- Full-text search

**Phase 3 – Mind Maps + Visuals (3 weeks)**  
- React Flow mind map with collaboration  
- Chart embedding blocks  
- Drawing canvas (Excalidraw or tldraw integration)

**Phase 4 – AI Layer (3–4 weeks)**  
- LLM proxy service  
- Summarize / rewrite / expand actions on selection  
- Generate mind map or database from notes  
- Workspace RAG (embed pages, semantic search)  
- Background job queue (Celery or RQ)

**Phase 5 – Polish & Multi-user Hardening (ongoing)**  
- Real-time presence everywhere  
- Advanced permissions & public sharing  
- Export (Markdown, PDF, PNG)  
- Mobile-responsive + offline-ish (service worker + local Yjs)  
- Admin / audit logs  
- Rate limiting, abuse prevention

### 5. Key Technical Decisions & Recommendations

- **Collaboration**: Prefer **Yjs** (CRDT) over operational transformation. It is battle-tested for this exact use case and works well with TipTap and React Flow.
- **Backend style**: Start with Flask + Flask-RESTful or Flask-Smorest. Keep services modular so you can later extract the AI or collab pieces.
- **Search**: Start with PostgreSQL `pg_trgm` + full-text. Add Meilisearch or Typesense later if needed.
- **AI**: Abstract the LLM calls behind a single service so you can swap providers or run local models. Use background workers for anything longer than a few seconds.
- **Frontend state**: Zustand or Jotai + React Query for server state. Yjs for collaborative documents.
- **Security** (especially relevant for you):  
  - Strict workspace-scoped permissions on every query  
  - Content Security Policy + sanitization on rich text  
  - Rate limiting + abuse detection on AI endpoints  
  - Audit logging for sensitive actions  
  - Optional self-hosted / air-gapped mode later

### 6. Potential Risks & Mitigations

| Risk                        | Mitigation                                      |
|-----------------------------|-------------------------------------------------|
| Real-time complexity        | Start with single-document collab, use Yjs     |
| Database flexibility vs performance | JSONB + careful indexing, later materialize hot paths |
| AI cost & latency           | Caching, user quotas, optional local models    |
| Scope creep                 | Strict phase gates; ship MVP documents + basic databases first |
| Multi-user permissions bugs | Comprehensive tests + Casbin or explicit query filters |

### 7. Recommended Starting Stack (Concrete)

- **Backend**: Flask 3.x, SQLAlchemy 2.0, Alembic, Flask-SocketIO, Celery + Redis, Pydantic for validation
- **Frontend**: Vite + React 18 + TypeScript + Tailwind + TipTap + React Flow + Yjs
- **Infra (dev)**: Docker Compose (Postgres + Redis + MinIO)
- **Later**: Kubernetes or simple VPS + Caddy, or self-hosted friendly packaging

---

This gives you a realistic path from zero to a usable multi-user creator platform without trying to clone the entire Notion surface area on day one. The highest-leverage early wins are solid auth/workspaces, collaborative documents with Yjs, and a flexible page + database model.

Would you like me to expand any phase into a more detailed technical spec, create a starter project structure (folder layout + key files), or prioritize a specific module (e.g., the AI layer or the database engine) first?
