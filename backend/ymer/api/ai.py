"""AI features: writing assist, semantic search, workspace Q&A and generation.

- ``GET  /api/ai/status``  which providers are on (the app hides AI when it is off)
- ``POST /api/workspaces/<ws>/pages/<id>/ai/assist`` {action, text?, instruction?}
  improve / fix / shorten / expand / summarize / bullets / custom on a piece of text.
  Editors only, except ``summarize``, which viewers may run. Without ``text``, works on
  the whole page.
- ``GET  /api/workspaces/<ws>/ai/search?q=``  semantic search over pages and rows
- ``POST /api/workspaces/<ws>/ai/ask`` {question, conversation_id?}  answer from the
  workspace, with numbered sources
- ``GET|DELETE /api/workspaces/<ws>/ai/conversations[/<id>]``  your own Q&A threads
- ``POST /api/workspaces/<ws>/ai/generate`` {kind, source_page_id? | text?, parent_id?,
  title?}  a new mind map or database from a page's notes (or pasted text)

What the model sees is limited to what the user can see: assist and generate check the
user's role on the page first, and search and Q&A only consider chunks of pages
``WorkspaceScope.visible_pages`` returns. Requests per user are rate limited.
"""

import json
import logging
import time
import uuid
from functools import wraps
from typing import Literal

from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from werkzeug.exceptions import BadGateway, NotFound, TooManyRequests, UnprocessableEntity

from ymer.ai import AiError, AiRefused, ChatRequest, get_ai, prompts
from ymer.ai.generate import create_database, mindmap_state
from ymer.ai.indexing import mark_dirty, page_chunks
from ymer.ai.retrieval import semantic_search
from ymer.api.pages import _require_can_create_under, page_json
from ymer.api.validation import parse_body
from ymer.api.workspaces import scope_for
from ymer.app import settings_for
from ymer.auth import current_user, login_required
from ymer.content import index_document, outline
from ymer.db import db_session
from ymer.documents import save_state
from ymer.extensions import get_redis
from ymer.models import (
    LIVE_PAGE_TYPES,
    AiConversation,
    AiMessage,
    DocumentContent,
    Page,
    PageType,
    Role,
)
from ymer.permissions import WorkspaceScope

log = logging.getLogger(__name__)

ai_status_bp = Blueprint("ai_status", __name__, url_prefix="/ai")
ai_bp = Blueprint("ai", __name__, url_prefix="/workspaces/<uuid:workspace_id>")

MAX_ASSIST_CHARS = 20_000
MAX_GENERATE_CHARS = 60_000
HISTORY_TURNS = 6


def _handle_ai_errors(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except AiRefused as exc:
            raise UnprocessableEntity(str(exc)) from exc
        except AiError as exc:
            raise BadGateway(str(exc)) from exc

    return wrapper


def _rate_limit() -> None:
    """At most ``YMER_AI_RATE_LIMIT`` AI requests per user per minute (Redis counter).

    If Redis is unreachable the request is allowed: AI should not go down with it.
    """
    limit = settings_for(current_app).ai_rate_limit
    if limit <= 0:
        return
    key = f"ymer:ai-rate:{current_user().id}:{int(time.time() // 60)}"
    try:
        redis = get_redis()
        count = redis.incr(key)
        if count == 1:
            redis.expire(key, 90)
    except Exception:  # noqa: BLE001 - fail open
        log.warning("AI rate limit check skipped: Redis unavailable.")
        return
    if count > limit:
        raise TooManyRequests("Too many AI requests. Wait a minute and try again.")


def ai_endpoint(view):
    """login_required + rate limit + AI errors as JSON (502, or 422 for a refusal)."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        _rate_limit()
        return view(*args, **kwargs)

    return login_required(_handle_ai_errors(wrapper))


# --- status ---------------------------------------------------------------------------


@ai_status_bp.get("/status")
@login_required
def status():
    ai = get_ai()
    settings = settings_for(current_app)
    return jsonify(
        enabled=ai.chat is not None,
        semantic_search=ai.embedder is not None,
        provider=ai.chat.name if ai.chat else None,
        model=ai.chat.model if ai.chat else None,
        embedding_model=ai.embedder.model if ai.embedder else None,
        demo=ai.demo,
        inline_indexing=settings.ai_index_inline,
    )


# --- helpers --------------------------------------------------------------------------


def page_text(page: Page) -> str:
    """What a page says, as structured text (database rows one per line)."""
    if page.type in LIVE_PAGE_TYPES:
        content = db_session.get(DocumentContent, page.id)
        return outline(content.state if content else None, page.type)
    if page.type is PageType.DATABASE:
        return "\n\n".join(text for _, text in page_chunks(db_session, page)[1:])
    return ""


def _snippet(content: str, limit: int = 240) -> str:
    body = content.split("\n", 1)[1] if "\n" in content else content
    body = " ".join(body.split())
    return body if len(body) <= limit else body[:limit].rsplit(" ", 1)[0] + "…"


# --- assist ---------------------------------------------------------------------------


class AssistBody(BaseModel):
    action: Literal["improve", "fix", "shorten", "expand", "summarize", "bullets", "custom"]
    text: str = Field(default="", max_length=MAX_ASSIST_CHARS)
    instruction: str = Field(default="", max_length=1000)


@ai_bp.post("/pages/<uuid:page_id>/ai/assist")
@ai_endpoint
def assist(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    body = parse_body(AssistBody)
    minimum = Role.VIEWER if body.action in prompts.READ_ONLY_ACTIONS else Role.EDITOR
    page = scope.get_page(page_id, minimum)
    chat = get_ai().require_chat()
    if body.action == "custom" and not body.instruction.strip():
        raise UnprocessableEntity("Say what the AI should do with the text.")
    text = body.text.strip() or page_text(page)
    if not text.strip():
        raise UnprocessableEntity("There is no text to work on yet.")
    if len(text) > MAX_ASSIST_CHARS:
        raise UnprocessableEntity(
            f"That is too much text for one request (over {MAX_ASSIST_CHARS:,} characters). "
            "Select a smaller part."
        )
    result = chat.complete(
        ChatRequest(
            task=f"assist.{body.action}",
            system=prompts.ASSIST_SYSTEM,
            messages=prompts.assist_messages(body.action, text, body.instruction, page.title),
            payload={"text": text, "instruction": body.instruction, "title": page.title},
            max_tokens=8000,
        )
    )
    return jsonify(action=body.action, text=result.strip(), demo=get_ai().demo)


# --- semantic search ------------------------------------------------------------------


def hit_json(hit) -> dict:
    title = hit.page.title or ""
    if hit.row_id is not None:
        # Row chunks read "Row in <database>\n<Title property>: <value>\n...".
        lines = hit.content.split("\n")
        title = lines[1].split(": ", 1)[-1] if len(lines) > 1 else ""
    return {
        "kind": "row" if hit.row_id else "page",
        "page_id": str(hit.page.id),
        "row_id": str(hit.row_id) if hit.row_id else None,
        "type": "row" if hit.row_id else hit.page.type.value,
        "title": title,
        "database_title": hit.page.title if hit.row_id else None,
        "snippet": _snippet(hit.content),
        "my_role": hit.role.value,
        "score": round(hit.score, 4),
    }


@ai_bp.get("/ai/search")
@login_required
def search(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    q = (request.args.get("q") or "").strip()[:500]
    try:
        limit = max(1, min(int(request.args.get("limit", 8)), 20))
    except ValueError:
        limit = 8
    if not q:
        return jsonify(results=[])
    embedder = get_ai().require_embedder()
    try:
        hits = semantic_search(scope, embedder, q, limit=limit)
    except AiError as exc:
        raise BadGateway(str(exc)) from exc
    return jsonify(results=[hit_json(h) for h in hits])


# --- Q&A ------------------------------------------------------------------------------


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None


def _conversation(scope: WorkspaceScope, conversation_id: uuid.UUID) -> AiConversation:
    conversation = db_session.scalar(
        select(AiConversation).where(
            AiConversation.id == conversation_id,
            AiConversation.workspace_id == scope.workspace.id,
            AiConversation.user_id == scope.user.id,
        )
    )
    if conversation is None:
        raise NotFound("Conversation not found.")
    return conversation


def conversation_json(c: AiConversation) -> dict:
    return {
        "id": str(c.id),
        "title": c.title,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


def message_json(m: AiMessage, visible: set[str] | None = None) -> dict:
    sources = m.sources or []
    if visible is not None:
        # Access can change after an answer was given: hide sources no longer visible.
        sources = [s for s in sources if s.get("page_id") in visible]
    return {
        "id": str(m.id),
        "role": m.role,
        "content": m.content,
        "sources": sources,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@ai_bp.post("/ai/ask")
@ai_endpoint
def ask(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    body = parse_body(AskBody)
    ai = get_ai()
    chat = ai.require_chat()
    embedder = ai.require_embedder()
    question = body.question.strip()

    if body.conversation_id:
        conversation = _conversation(scope, body.conversation_id)
        previous = list(
            db_session.scalars(
                select(AiMessage)
                .where(AiMessage.conversation_id == conversation.id)
                .order_by(AiMessage.created_at.desc())
                .limit(HISTORY_TURNS * 2)
            )
        )[::-1]
    else:
        conversation = AiConversation(
            workspace_id=workspace_id, user_id=scope.user.id, title=question[:120]
        )
        db_session.add(conversation)
        previous = []

    hits = semantic_search(scope, embedder, question, limit=5)
    # A follow-up ("and who owns it?") may only make sense with the previous question,
    # so also search with both and add what that finds.
    last_question = next((m.content for m in reversed(previous) if m.role == "user"), "")
    if last_question:
        seen = {(h.page.id, h.row_id) for h in hits}
        for hit in semantic_search(scope, embedder, f"{last_question}\n{question}", limit=3):
            if (hit.page.id, hit.row_id) not in seen:
                hits.append(hit)
    sources = [
        {
            "n": n,
            "page_id": str(h.page.id),
            "row_id": str(h.row_id) if h.row_id else None,
            "type": "row" if h.row_id else h.page.type.value,
            "title": h.page.title or "Untitled",
            "snippet": _snippet(h.content),
            "text": h.content,
        }
        for n, h in enumerate(hits, start=1)
    ]
    history = [{"role": m.role, "content": m.content} for m in previous]
    answer = chat.complete(
        ChatRequest(
            task="ask",
            system=prompts.ASK_SYSTEM,
            messages=prompts.ask_messages(history, question, sources),
            payload={"question": question, "sources": sources},
            max_tokens=4000,
        )
    ).strip()

    db_session.flush()
    asked = AiMessage(conversation_id=conversation.id, role="user", content=question)
    db_session.add(asked)
    db_session.flush()  # keep created_at order between the two
    reply = AiMessage(
        conversation_id=conversation.id,
        role="assistant",
        content=answer,
        sources=[{k: v for k, v in s.items() if k != "text"} for s in sources],
    )
    db_session.add(reply)
    conversation.updated_at = func.now()  # bump it to the top of the list
    db_session.commit()
    db_session.refresh(conversation)
    return jsonify(
        conversation=conversation_json(conversation),
        question=message_json(asked),
        answer=message_json(reply),
        demo=ai.demo,
    )


@ai_bp.get("/ai/conversations")
@login_required
def list_conversations(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    rows = db_session.scalars(
        select(AiConversation)
        .where(
            AiConversation.workspace_id == workspace_id,
            AiConversation.user_id == scope.user.id,
        )
        .order_by(AiConversation.updated_at.desc())
        .limit(100)
    )
    return jsonify(conversations=[conversation_json(c) for c in rows])


@ai_bp.get("/ai/conversations/<uuid:conversation_id>")
@login_required
def get_conversation(workspace_id: uuid.UUID, conversation_id: uuid.UUID):
    scope = scope_for(workspace_id)
    conversation = _conversation(scope, conversation_id)
    visible = {str(p.id) for p, _ in scope.visible_pages()}
    messages = db_session.scalars(
        select(AiMessage)
        .where(AiMessage.conversation_id == conversation.id)
        .order_by(AiMessage.created_at)
    )
    return jsonify(
        conversation=conversation_json(conversation),
        messages=[message_json(m, visible) for m in messages],
    )


@ai_bp.delete("/ai/conversations/<uuid:conversation_id>")
@login_required
def delete_conversation(workspace_id: uuid.UUID, conversation_id: uuid.UUID):
    scope = scope_for(workspace_id)
    db_session.delete(_conversation(scope, conversation_id))
    db_session.commit()
    return "", 204


# --- generate -------------------------------------------------------------------------


class GenerateBody(BaseModel):
    kind: Literal["mindmap", "database"]
    source_page_id: uuid.UUID | None = None
    text: str = Field(default="", max_length=MAX_GENERATE_CHARS)
    parent_id: uuid.UUID | None = None
    title: str = Field(default="", max_length=500)


@ai_bp.post("/ai/generate")
@ai_endpoint
def generate(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    body = parse_body(GenerateBody)
    chat = get_ai().require_chat()
    _require_can_create_under(scope, body.parent_id)

    source_title = body.title
    text = body.text.strip()
    if body.source_page_id is not None:
        source = scope.get_page(body.source_page_id, Role.VIEWER)
        source_title = source_title or source.title
        text = page_text(source)
    if not text:
        raise UnprocessableEntity("There are no notes to work from yet.")
    if len(text) > MAX_GENERATE_CHARS:
        raise UnprocessableEntity(
            f"Those notes are too long for one request (over {MAX_GENERATE_CHARS:,} "
            "characters). Split them across pages, or paste a part."
        )

    schema = prompts.MINDMAP_SCHEMA if body.kind == "mindmap" else prompts.DATABASE_SCHEMA
    raw = chat.complete(
        ChatRequest(
            task=f"generate.{body.kind}",
            system=prompts.GENERATE_SYSTEM,
            messages=prompts.generate_messages(body.kind, source_title, text),
            payload={"title": source_title, "text": text},
            schema=schema,
            max_tokens=16000,
        )
    )
    try:
        spec = json.loads(raw)
        if not isinstance(spec, dict):
            raise ValueError
    except ValueError as exc:
        raise AiError("The model's answer was not in the expected format. Try again.") from exc

    title = body.title.strip()
    if not title:
        # Name it after the model's title, but never exactly like the page it came from.
        title = str(spec.get("title") or "").strip()[:450] or source_title or "Notes"
        if title.casefold() == (source_title or "").strip().casefold():
            title += " (mind map)" if body.kind == "mindmap" else " (table)"
    user = current_user()
    page = Page(
        workspace_id=workspace_id,
        parent_id=body.parent_id,
        type=PageType.MINDMAP if body.kind == "mindmap" else PageType.DATABASE,
        title=title[:500],
        created_by_id=user.id,
    )
    db_session.add(page)
    db_session.flush()
    if body.kind == "mindmap":
        spec["title"] = spec.get("title") or title
        state, count = mindmap_state(spec)
        save_state(
            db_session,
            page.id,
            state,
            user_id=user.id,
            version_interval=settings_for(current_app).version_interval,
        )
        index_document(db_session, page, state)
        summary = {"ideas": count}
    else:
        props, rows = create_database(db_session, page, spec, user_id=user.id)
        summary = {"properties": props, "rows": rows}
    mark_dirty(db_session, [page.id])
    db_session.commit()
    return jsonify(page=page_json(page, scope.page_role(page)), summary=summary), 201
