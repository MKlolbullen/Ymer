"""Model providers behind one small interface.

Every model call in Ymer goes through :class:`ChatProvider` or :class:`Embedder`, so the
model can be swapped by configuration (``YMER_AI_PROVIDER``, ``YMER_AI_EMBEDDING_PROVIDER``)
without touching the features that use it.

- ``anthropic``: Claude through the official SDK. Default model ``claude-opus-5-5``.
  Requests stream (long outputs never hit an HTTP timeout), ask for JSON with structured
  outputs when a schema is given, and opt into server-side refusal fallbacks.
- ``openai``: any OpenAI-compatible ``/chat/completions`` and ``/embeddings`` server, which
  covers local models (Ollama, vLLM, LM Studio, llama.cpp) and most hosted gateways.
- ``fake``: deterministic, offline stand-ins (``ymer.ai.fake``) so the app, its tests and
  its screenshots work without any key. The UI labels their output as a demo.
"""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)


class AiError(Exception):
    """A model call failed; the message is safe to show to the user."""


class AiRefused(AiError):
    """The model declined the request."""


@dataclass
class ChatRequest:
    """One model call.

    ``task`` names what is being asked (``assist.summarize``, ``ask``, ``generate.mindmap``)
    and ``payload`` carries its inputs as data. Real providers only read ``system`` and
    ``messages``; the fake provider works from ``task`` and ``payload``.
    """

    task: str
    system: str
    messages: list[dict[str, str]]
    payload: dict[str, Any] = field(default_factory=dict)
    # JSON Schema the answer must match; the reply is then a JSON string.
    schema: dict[str, Any] | None = None
    max_tokens: int = 8000


class ChatProvider(Protocol):
    name: str
    model: str

    def complete(self, request: ChatRequest) -> str: ...


class Embedder(Protocol):
    name: str
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


# --- Claude ---------------------------------------------------------------------------


class AnthropicProvider:
    name = "anthropic"
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(
        self,
        *,
        model: str,
        effort: str | None,
        api_key: str | None,
        base_url: str | None,
        timeout: float,
        fallbacks: bool,
        follow_redirects: bool = True,
    ) -> None:
        import anthropic  # imported lazily so the fake provider needs no SDK

        self.model = model
        self.effort = effort
        self.fallbacks = fallbacks
        # With no api_key the SDK reads ANTHROPIC_API_KEY (or an `ant auth login` profile).
        self.client = anthropic.Anthropic(
            api_key=api_key or None,
            base_url=base_url or None,
            timeout=timeout,
            max_retries=2,
            http_client=None
            if follow_redirects
            else anthropic.DefaultHttpxClient(follow_redirects=False),
        )
        self._anthropic = anthropic

    def complete(self, request: ChatRequest) -> str:
        output_config: dict[str, Any] = {"effort": self.effort} if self.effort else {}
        if request.schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.schema}
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": request.messages,
        }
        if output_config:
            kwargs["output_config"] = output_config
        if self.fallbacks:
            kwargs["betas"] = [self.FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        errors = self._anthropic
        try:
            with self.client.beta.messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except errors.AuthenticationError as exc:
            raise AiError("The AI provider rejected the API key.") from exc
        except errors.RateLimitError as exc:
            raise AiError("The AI provider is rate limiting requests. Try again shortly.") from exc
        except errors.APIStatusError as exc:
            log.warning("Claude request failed: %s", exc.status_code)
            raise AiError(f"The AI provider answered with an error ({exc.status_code}).") from exc
        except errors.APIConnectionError as exc:
            raise AiError("Could not reach the AI provider.") from exc
        if message.stop_reason == "refusal":
            raise AiRefused("The model declined this request.")
        text = "".join(block.text for block in message.content if block.type == "text")
        if message.stop_reason == "max_tokens" and request.schema is not None:
            raise AiError("The answer was too long and got cut off. Try with less text.")
        return text


# --- OpenAI-compatible (local models and gateways) -------------------------------------


class _NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post_json(url: str, body: dict, api_key: str | None, timeout: float) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        opener = urllib.request.build_opener(_NoCredentialRedirect) if api_key else None
        send = opener.open if opener else urllib.request.urlopen
        with send(req, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        log.warning("AI endpoint %s answered %s", url, exc.code)
        raise AiError(f"The AI provider answered with an error ({exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AiError("Could not reach the AI provider.") from exc


class OpenAICompatibleProvider:
    name = "openai"

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None,
        timeout: float,
        name: str = "openai",
        hosted: bool = False,
    ) -> None:
        self.name = name
        self.hosted = hosted
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, request: ChatRequest) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            (
                "max_completion_tokens" if self.hosted and self.name == "openai" else "max_tokens"
            ): request.max_tokens,
            "messages": [{"role": "system", "content": request.system}, *request.messages],
        }
        if request.schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": request.schema, "strict": True},
            }
        data = _post_json(f"{self.base_url}/chat/completions", body, self.api_key, self.timeout)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise AiError("The AI provider sent an answer Ymer could not read.") from exc


class OpenAICompatibleEmbedder:
    name = "openai"

    def __init__(self, *, model: str, base_url: str, api_key: str | None, timeout: float) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        data = _post_json(
            f"{self.base_url}/embeddings",
            {"model": self.model, "input": texts},
            self.api_key,
            self.timeout,
        )
        try:
            items = sorted(data["data"], key=lambda item: item.get("index", 0))
            return [list(map(float, item["embedding"])) for item in items]
        except (KeyError, TypeError, ValueError) as exc:
            raise AiError("The embedding provider sent an answer Ymer could not read.") from exc
