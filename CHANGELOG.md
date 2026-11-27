# Changelog

Ymer's version lives in `VERSION` and is copied into every package by
`python scripts/version.py set <version>`. Pushing a `v<version>` tag builds the
release (see [Releasing](DEVELOPMENT.md#releasing)). The release workflow takes
its notes from the section below whose heading names that version, so add one
before tagging.

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

Known gaps for this beta: no installers yet (run from source with `./install.sh`),
and pushes to GitHub/GitLab and a real Claude key are untested in automated runs.
