"""The AI layer: one place every model call goes through.

- :mod:`ymer.ai.providers`: the chat and embedding interfaces and the real providers
- :mod:`ymer.ai.fake`: offline stand-ins used by default in development and tests
- :mod:`ymer.ai.prompts`: system prompts and output schemas for each feature
- :mod:`ymer.ai.indexing`: chunking, embedding and the indexing queue
- :mod:`ymer.ai.retrieval`: permission-filtered semantic search over the chunks
- :mod:`ymer.ai.generate`: turning model output into mind maps and databases

Everything the AI reads goes through :class:`ymer.permissions.WorkspaceScope`: retrieval
only considers chunks of pages the user can see, and features that read a page check the
user's role on it first.
"""

from dataclasses import dataclass

from flask import Flask, current_app
from werkzeug.exceptions import ServiceUnavailable

from ymer.ai.fake import FakeChat, HashEmbedder
from ymer.ai.providers import (
    AiError,
    AiRefused,
    AnthropicProvider,
    ChatProvider,
    ChatRequest,
    Embedder,
    OpenAICompatibleEmbedder,
    OpenAICompatibleProvider,
)
from ymer.config import Settings

__all__ = [
    "AiError",
    "AiRefused",
    "AiServices",
    "AiUnavailable",
    "ChatRequest",
    "build_services",
    "get_ai",
]


class AiUnavailable(ServiceUnavailable):
    pass


@dataclass
class AiServices:
    chat: ChatProvider | None
    embedder: Embedder | None

    @property
    def demo(self) -> bool:
        return isinstance(self.chat, FakeChat) or isinstance(self.embedder, HashEmbedder)

    def require_chat(self) -> ChatProvider:
        if self.chat is None:
            raise AiUnavailable("AI features are turned off on this server.")
        return self.chat

    def require_embedder(self) -> Embedder:
        if self.embedder is None:
            raise AiUnavailable("Semantic search is turned off on this server.")
        return self.embedder


def build_services(settings: Settings) -> AiServices:
    chat: ChatProvider | None = None
    match settings.ai_provider:
        case "fake":
            chat = FakeChat()
        case "anthropic":
            chat = AnthropicProvider(
                model=settings.ai_model,
                effort=settings.ai_effort,
                api_key=settings.ai_api_key,
                base_url=settings.ai_base_url,
                timeout=settings.ai_timeout,
                fallbacks=settings.ai_fallbacks,
            )
        case "openai":
            if not settings.ai_base_url:
                raise RuntimeError("YMER_AI_PROVIDER=openai needs YMER_AI_BASE_URL.")
            chat = OpenAICompatibleProvider(
                model=settings.ai_model,
                base_url=settings.ai_base_url,
                api_key=settings.ai_api_key,
                timeout=settings.ai_timeout,
            )

    embedder: Embedder | None = None
    match settings.ai_embedding_provider:
        case "fake":
            embedder = HashEmbedder(settings.ai_embedding_model)
        case "openai":
            base_url = settings.ai_embedding_base_url or settings.ai_base_url
            if not base_url:
                raise RuntimeError("YMER_AI_EMBEDDING_PROVIDER=openai needs a base URL.")
            embedder = OpenAICompatibleEmbedder(
                model=settings.ai_embedding_model,
                base_url=base_url,
                api_key=settings.ai_embedding_api_key or settings.ai_api_key,
                timeout=settings.ai_timeout,
            )
    return AiServices(chat=chat, embedder=embedder)


def init_ai(app: Flask, settings: Settings) -> None:
    app.extensions["ymer_ai"] = build_services(settings)


def get_ai() -> AiServices:
    return current_app.extensions["ymer_ai"]
