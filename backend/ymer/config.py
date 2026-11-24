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

    # Comma-separated in the environment: YMER_CORS_ORIGINS=http://a,http://b
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # JWT lifetimes in seconds. Access tokens are short-lived; refresh tokens mint new ones.
    jwt_access_ttl: int = 15 * 60
    jwt_refresh_ttl: int = 30 * 24 * 60 * 60

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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
