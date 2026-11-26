"""Application settings, loaded from environment variables (prefix ``YMER_``).

A ``.env`` file in the repository root or in ``backend/`` is read automatically
in development. Values passed to ``create_app(overrides)`` win over both.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YMER_",
        env_file=(_REPO_ROOT / ".env", _BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    secret_key: str = "change-me"

    database_url: str = "postgresql+psycopg://ymer:ymer@localhost:5432/ymer"
    test_database_url: str = "postgresql+psycopg://ymer:ymer@localhost:5432/ymer_test"
    database_echo: bool = False

    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_access_key: str = "ymer"
    s3_secret_key: str = "ymer-dev-secret"
    s3_bucket: str = "ymer"
    s3_region: str = "us-east-1"
    # Seconds an upload or download to S3 may wait for the storage server.
    s3_timeout: float = 30.0

    # Comma-separated in the environment: YMER_CORS_ORIGINS=http://a,http://b
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # JWT lifetimes in seconds. Access tokens are short-lived; refresh tokens mint new ones.
    jwt_access_ttl: int = 15 * 60
    jwt_refresh_ttl: int = 30 * 24 * 60 * 60

    # Browser sessions live in httpOnly cookies (ymer.auth.cookies). Secure cookies are only
    # sent over HTTPS; unset means "Secure in production". Leave the domain unset unless the
    # API and the app are on different subdomains of one site (then e.g. ".example.com").
    cookie_secure: bool | None = None
    cookie_domain: str | None = None
    # Number of reverse proxies in front of Flask whose X-Forwarded-For / -Proto headers
    # are trusted. Rate limits and the audit log use the client address they report.
    trusted_proxies: int = 0

    # --- Rate limits (ymer.ratelimit), counted in Redis -------------------------------
    rate_limit_enabled: bool = True
    # Any /api request, per signed-in user (or per address when signed out), per minute.
    rate_limit_api: int = 600
    # Sign-in attempts per address per minute, and failed sign-ins per email per 15 minutes.
    rate_limit_login: int = 20
    rate_limit_login_failures: int = 10
    # New accounts per address per hour.
    rate_limit_register: int = 10
    # Uploads per user per minute, and Factory git pushes per user per hour.
    rate_limit_uploads: int = 60
    rate_limit_git_push: int = 20

    # Audit events older than this are removed by `flask --app wsgi prune-audit`.
    audit_retention_days: int = 365

    # Browser-facing S3 endpoint for presigned download URLs, when it differs from
    # s3_endpoint_url (e.g. the backend reaches MinIO as http://minio:9000 inside Docker).
    s3_public_endpoint_url: str | None = None
    # Largest upload accepted, in bytes.
    max_upload_bytes: int = 25 * 1024 * 1024

    # Shared secret the collab service (collab/) sends on /internal/collab/* requests.
    collab_secret: str = "change-me-collab"
    # An automatic version snapshot is taken on save when the newest one is older than this.
    version_interval: int = 10 * 60

    # Seconds each dependency check in /api/health/ready may take.
    health_check_timeout: float = 2.0

    # --- AI layer (ymer.ai) -----------------------------------------------------------
    # Which model answers chat-style requests (assist, Q&A, generation):
    #   fake       deterministic stand-in, no network, no key; for development and tests
    #   anthropic  Claude through the Anthropic API (key: ANTHROPIC_API_KEY or YMER_AI_API_KEY)
    #   openai     any OpenAI-compatible endpoint (Ollama, vLLM, LM Studio...) at YMER_AI_BASE_URL
    #   off        AI features are hidden and their routes answer 503
    ai_provider: Literal["fake", "anthropic", "openai", "off"] = "fake"
    ai_model: str = "claude-opus-5-5"
    # Effort for Claude (low / medium / high / xhigh / max). Editor assists are short tasks.
    ai_effort: str = "medium"
    # Retry a declined Claude request on Anthropic's recommended fallback model.
    ai_fallbacks: bool = True
    ai_credential_secret: str | None = None
    ai_api_key: str | None = None
    ai_base_url: str | None = None
    ai_timeout: float = 120.0
    # Embeddings for semantic search and Q&A. Claude has no embeddings API, so this is
    # separate: "fake" (hashed bag of words, no network) or "openai" (an OpenAI-compatible
    # /embeddings endpoint, e.g. Ollama with nomic-embed-text).
    ai_embedding_provider: Literal["fake", "openai", "off"] = "fake"
    ai_embedding_model: str = "hash-256"
    ai_embedding_base_url: str | None = None
    ai_embedding_api_key: str | None = None
    # Index pages for semantic search in the request that changed them (handy in
    # development) instead of leaving them to `flask --app wsgi ai-worker`.
    ai_index_inline: bool = False
    # AI requests per user per minute (0 = unlimited). Counted in Redis.
    ai_rate_limit: int = 30

    # --- Factory builds (ymer.factory) ------------------------------------------------
    # Where code the AI writes is run (tests, builds):
    #   fake    nothing runs: test files are only checked for syntax; for development/tests
    #   docker  one locked-down container per command (no network, CPU/memory/time limits)
    #   modal   the same, hosted on Modal (modal.com): the server needs no Docker
    #   off     builds are turned off
    factory_sandbox: Literal["fake", "docker", "modal", "off"] = "fake"
    factory_docker_bin: str = "docker"
    factory_memory: str = "1g"
    factory_cpus: str = "1"
    # Modal sandbox (factory_sandbox="modal"). Credentials default to Modal's own
    # MODAL_TOKEN_ID / MODAL_TOKEN_SECRET environment variables.
    factory_modal_app: str = "ymer-factory"
    factory_modal_token_id: str | None = None
    factory_modal_token_secret: str | None = None
    # Images are built by Modal from infra/factory/<stack>.Dockerfile unless a registry
    # holding the pushed images is set (e.g. ghcr.io/you, giving ghcr.io/you/python-cli:1).
    factory_modal_dockerfiles: str | None = None
    factory_modal_registry: str | None = None
    factory_command_timeout: int = 300
    # Run builds in the request that starts them (development) instead of in
    # `flask --app wsgi factory-worker`.
    factory_run_inline: bool = False
    # Budgets per build run: model turns in total, turns per task, and tokens.
    factory_max_steps: int = 300
    factory_task_steps: int = 30
    factory_max_tokens: int = 3_000_000
    # Tokens all Factory builds in one workspace may use per calendar month (UTC), unless
    # the workspace's owner sets its own limit. 0 means no limit.
    factory_workspace_monthly_tokens: int = 20_000_000
    # Delivery (F4). Releases are pushed over HTTPS to these hosts only, with a token the
    # person types in for that push (never stored).
    factory_git_hosts: str = "github.com,gitlab.com,bitbucket.org,codeberg.org"
    # Also allow file:// remotes (tests and local experiments only).
    factory_git_allow_local: bool = False
    # CA bundle git should trust, for a self-hosted Git server with a private certificate.
    factory_git_ca_bundle: str | None = None
    factory_git_timeout: int = 120
    # How long a preview link works.
    factory_preview_minutes: int = 60

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
