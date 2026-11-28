# Changelog

Ymer's version lives in `VERSION` and is copied into every package by
`python scripts/version.py set <version>`. Pushing a `v<version>` tag builds the
release (see [Releasing](DEVELOPMENT.md#releasing)). The release workflow takes
its notes from the section below whose heading names that version, so add one
before tagging. If the section is missing or empty, GitHub generates the notes.

## [0.1.0] - 2026-11-28

- **Production on one Linux VM** ([guide](docs/production.md)):
  - HTTPS with automatic Let's Encrypt certificates through Caddy: `ymerctl https <domain> <email>`, or `YMER_DOMAIN` with `ymerctl init`.
  - `ymerctl backup` puts a database dump and every stored file in one archive, optionally encrypted (`YMER_BACKUP_PASSPHRASE`) and copied to any S3-compatible bucket (`YMER_BACKUP_S3_*`). `ymerctl restore` checks the archive, takes a safety backup and replaces all data. The `.deb` backs up daily with `ymer-backup.timer`; `ymerctl update` backs up first.
  - Monitoring (`YMER_MONITORING=true`): Prometheus metrics from the API (`/metrics`, internal only) and collab service, Postgres, MinIO and host exporters, 15 alert rules sent through Alertmanager to Slack, a webhook or email, and a Grafana "Ymer overview" dashboard.
  - JSON logs (`YMER_LOG_FORMAT=json`) with an `X-Request-ID` that follows each request from Caddy to the API.
  - `ymerctl check` lists what still stands between a server and production.

- **Phone apps:** Ymer for Android (`.apk`, `.aab`) and iOS (`.ipa`, opt-in build on macOS). They bundle the web app and reach your server through a small relay inside the app, so they start and edit offline, even after a restart. Pick the server on first launch; **Change server** is in the sidebar.

- Private installations can disable public sign-ups with `YMER_REGISTRATION_ENABLED=false` and create individual accounts with `flask --app wsgi create-user`. Registration stays open by default.
- The [installation guide](INSTALLATION.MD) covers private Vultr hosting and setting up two owners with equal workspace permissions.
- Release checks exercise both registration modes against the packaged server bundle. The private check verifies that a third sign-up is rejected and both provisioned owners can independently rename their shared workspace.

## [0.1.0-beta.1] - unreleased

The first beta. Everything in the README's "What works today" is in it:

- **Collaborative documents:** live editing with cursors over Hocuspocus, comments, version history, file uploads to S3-compatible storage.
- **Notebooks and databases:** table, board and chart views, relations, rollups (filtered and two-step), formulas, backlinks and full-text search.
- **Mind maps, drawings and charts** as pages and as blocks inside documents.
- **Templates and import:** workspace templates, Notion and Markdown import with relations kept.
- **AI layer:** writing assist, ask your workspace, semantic search, AI-built databases and mind maps (needs an Anthropic API key).
- **Factory:** brief, architecture, build in a Docker or hosted (Modal) sandbox, checks (lint, build, tests, smoke), review, delivery to GitHub or GitLab, and iterating on a built app.
- **Phones and offline:** installable PWA that keeps editing offline and resyncs.
- **Hardening:** httpOnly cookie sessions with CSRF, rate limits and an activity log.

Installers, new in this beta:

- **Docker images** `ghcr.io/mklolbullen/ymer-api`, `-collab` and `-web` for amd64 and arm64.
- **Server bundle** (`ymer-server-<version>.tar.gz`/`.zip`): Docker Compose with PostgreSQL, Redis and MinIO,
  a single port (8080), generated secrets and `ymerctl` (start, stop, status, logs, update).
- **Debian/Ubuntu package** (`ymer_<version>_all.deb`): the bundle as the `ymer` systemd service.
- **Windows installer** (`Ymer-Setup-<version>.exe`): the bundle with Start menu shortcuts, on Docker Desktop.
- **Desktop client** (`Ymer-Desktop-*`): a Tauri app for Windows, macOS and Linux that opens a Ymer server.

Known gaps: the installers and the desktop client are not code-signed yet, so Windows SmartScreen and macOS
Gatekeeper warn on first launch (on macOS, right-click the app and choose Open). The bundle's Factory cannot use
the Docker sandbox. Pushes to GitHub/GitLab and a real Claude key are untested in automated runs.
