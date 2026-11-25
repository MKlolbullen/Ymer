"""Account-scoped chat profiles. Embeddings always use the installation's configuration."""

import os
import uuid
from enum import StrEnum

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import current_app, g
from werkzeug.exceptions import ServiceUnavailable

from ymer.ai import AiError, AiServices, get_ai
from ymer.ai.providers import AnthropicProvider, ChatProvider, OpenAICompatibleProvider
from ymer.app import settings_for
from ymer.auth import current_user
from ymer.config import Settings
from ymer.db import db_session
from ymer.models.user import UserAiProfile


class Provider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    XAI = "xai"
    GEMINI = "gemini"
    VENICE = "venice"


ENDPOINTS = {
    Provider.ANTHROPIC: "https://api.anthropic.com",
    Provider.OPENAI: "https://api.openai.com/v1",
    Provider.XAI: "https://api.x.ai/v1",
    Provider.GEMINI: "https://generativelanguage.googleapis.com/v1beta/openai",
    Provider.VENICE: "https://api.venice.ai/api/v1",
}


def _cipher(settings: Settings) -> AESGCM:
    secret = settings.ai_credential_secret or settings.secret_key
    if len(secret) < 32:
        raise AiError(
            "Set a random YMER_AI_CREDENTIAL_SECRET of at least 32 characters on the server."
        )
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"ymer/personal-ai/v1").derive(
        secret.encode()
    )
    return AESGCM(key)


def encrypt_key(settings: Settings, user_id: uuid.UUID, provider: str, key: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + _cipher(settings).encrypt(nonce, key.encode(), f"{user_id}/{provider}".encode())


def profile_chat(
    settings: Settings, profile: UserAiProfile | None, model: str | None = None
) -> ChatProvider:
    if profile is None:
        raise AiError("The saved provider key was removed. Choose a provider in Settings.")
    model = model or profile.model
    provider = Provider(profile.provider)
    data = profile.encrypted_key
    try:
        key = (
            _cipher(settings)
            .decrypt(data[:12], data[12:], f"{profile.user_id}/{provider}".encode())
            .decode()
        )
    except (InvalidTag, ValueError) as exc:
        raise AiError("Cannot decrypt this provider key. Replace it in Settings.") from exc
    if provider == Provider.ANTHROPIC:
        return AnthropicProvider(
            model=model,
            api_key=key,
            base_url=ENDPOINTS[provider],
            effort=None,
            fallbacks=False,
            follow_redirects=False,
            timeout=settings.ai_timeout,
        )
    return OpenAICompatibleProvider(
        name=provider.value,
        model=model,
        base_url=ENDPOINTS[provider],
        api_key=key,
        timeout=settings.ai_timeout,
        hosted=True,
    )


def user_ai() -> AiServices:
    default = get_ai()
    user = current_user()
    if default.chat is None or user.ai_provider is None:
        return default
    if "personal_ai" not in g:
        profile = db_session.get(UserAiProfile, (user.id, user.ai_provider))
        try:
            chat = profile_chat(settings_for(current_app), profile)
        except AiError as exc:
            raise ServiceUnavailable(str(exc)) from exc
        g.personal_ai = AiServices(chat=chat, embedder=default.embedder)
    return g.personal_ai
