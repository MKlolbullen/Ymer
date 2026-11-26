"""Keeping embeddings in step with the workspace.

When a page changes (a collab save, a rename, a database row edit) it is marked in
``ai_index_queue``. The worker (``flask --app wsgi ai-worker``) takes marked pages,
splits them into chunks, embeds the chunks that changed, and replaces the page's rows
in ``ai_chunks``. Chunks whose text did not change keep their embedding, so an edit to
one paragraph re-embeds one chunk, not the whole page.

With ``YMER_AI_INDEX_INLINE=true`` a marked page is indexed in the same request, which
is convenient in development with the fake embedder.

What is indexed: every page's title; documents, mind maps and drawings through their
outline (``ymer.content.outline``); each database row as one chunk under its database.
"""

import hashlib
import logging
import re
import uuid
from collections.abc import Iterable

from flask import current_app, has_app_context
from sqlalchemy import delete, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ymer.ai.providers import AiError, Embedder
from ymer.content import outline
from ymer.models import (
    LIVE_PAGE_TYPES,
    AiChunk,
    AiIndexJob,
    DatabaseProperty,
    DatabaseRow,
    DocumentContent,
    Page,
    PageType,
    PropertyType,
)

log = logging.getLogger(__name__)

CHUNK_CHARS = 1000
EMBED_BATCH = 64
MAX_ATTEMPTS = 5


# --- chunking -------------------------------------------------------------------------


def split_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split text into pieces of about ``size`` characters on line and sentence ends."""
    units: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) <= size:
            units.append(line)
            continue
        units.extend(s for s in re.split(r"(?<=[.!?])\s+", line) if s)
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for unit in units:
        while len(unit) > size:  # a single very long sentence
            chunks.append(unit[:size])
            unit = unit[size:]
        if current and length + len(unit) + 1 > size:
            chunks.append("\n".join(current))
            # Carry the last line over so a fact split across the boundary keeps context.
            current = current[-1:] if len(current[-1]) < size // 4 else []
            length = sum(len(c) + 1 for c in current)
        current.append(unit)
        length += len(unit) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def row_text(props: list[DatabaseProperty], values: dict) -> str:
    """A row as "Property: value" lines, with select options by name."""
    lines = []
    for prop in props:
        value = values.get(str(prop.id))
        if value in (None, "", []):
            continue
        if prop.type in (PropertyType.SELECT, PropertyType.MULTI_SELECT):
            names = {o["id"]: o["name"] for o in prop.config.get("options", [])}
            ids = value if isinstance(value, list) else [value]
            value = ", ".join(names.get(i, "") for i in ids if names.get(i))
        elif prop.type is PropertyType.CHECKBOX:
            value = "yes" if value else "no"
        elif prop.type is PropertyType.PERSON:
            # Member ids say nothing useful; names of people who are not members do.
            value = ", ".join(p["name"] for p in value if isinstance(p, dict) and p.get("name"))
        elif prop.type is PropertyType.DATE_RANGE:
            value = " to ".join(v for v in (value.get("start"), value.get("end")) if v)
        elif prop.type is PropertyType.FILE:
            value = ", ".join(f.get("name") or "" for f in value)
        if value not in (None, ""):
            lines.append(f"{prop.name}: {value}")
    return "\n".join(lines)


def page_chunks(session: Session, page: Page) -> list[tuple[uuid.UUID | None, str]]:
    """(row id, text) for each chunk of a page. Each chunk names its page."""
    title = page.title or "Untitled"
    header = f"{title} ({page.type.value})"
    if page.type in LIVE_PAGE_TYPES:
        content = session.get(DocumentContent, page.id)
        text = outline(content.state if content else None, page.type)
        pieces = split_text(text)
        if not pieces:
            return [(None, header)]
        return [(None, f"{header}\n{piece}") for piece in pieces]
    if page.type is PageType.DATABASE:
        props = list(
            session.scalars(
                select(DatabaseProperty)
                .where(DatabaseProperty.database_id == page.id)
                .order_by(DatabaseProperty.position)
            )
        )
        chunks: list[tuple[uuid.UUID | None, str]] = [
            (None, f"{header}\nProperties: {', '.join(p.name for p in props)}")
        ]
        rows = session.scalars(
            select(DatabaseRow)
            .where(DatabaseRow.database_id == page.id)
            .order_by(DatabaseRow.position)
        )
        for row in rows:
            text = row_text(props, row.values)
            if text:
                chunks.append((row.id, f"Row in {title}\n{text}"[: CHUNK_CHARS * 2]))
        return chunks
    return [(None, header)]


# --- indexing -------------------------------------------------------------------------


def _hash(model: str, text: str) -> str:
    return hashlib.sha256(f"{model}\0{text}".encode()).hexdigest()


def index_page(session: Session, page: Page, embedder: Embedder) -> int:
    """Bring ``page``'s chunks up to date. Returns how many were embedded. Caller commits."""
    wanted = page_chunks(session, page)
    existing = {
        c.content_hash: c
        for c in session.scalars(
            select(AiChunk).where(AiChunk.page_id == page.id, AiChunk.model == embedder.model)
        )
    }
    keep: set[uuid.UUID] = set()
    new: list[tuple[int, uuid.UUID | None, str, str]] = []
    for position, (row_id, text) in enumerate(wanted):
        digest = _hash(embedder.model, text)
        chunk = existing.get(digest)
        if chunk is not None and chunk.id not in keep:
            keep.add(chunk.id)
            chunk.position = position
        else:
            new.append((position, row_id, text, digest))
    vectors: list[list[float]] = []
    for start in range(0, len(new), EMBED_BATCH):
        vectors.extend(embedder.embed([text for _, _, text, _ in new[start : start + EMBED_BATCH]]))
    session.execute(
        delete(AiChunk).where(
            AiChunk.page_id == page.id,
            ~AiChunk.id.in_(keep) if keep else true(),
        )
    )
    for (position, row_id, text, digest), vector in zip(new, vectors, strict=True):
        session.add(
            AiChunk(
                workspace_id=page.workspace_id,
                page_id=page.id,
                row_id=row_id,
                position=position,
                content=text,
                content_hash=digest,
                model=embedder.model,
                embedding=vector,
            )
        )
    return len(new)


# --- the queue ------------------------------------------------------------------------


def mark_dirty(session: Session, page_ids: Iterable[uuid.UUID]) -> None:
    """Queue pages for re-indexing (or index them now in inline mode). Caller commits."""
    ids = list(dict.fromkeys(page_ids))
    if not ids:
        return
    session.execute(
        insert(AiIndexJob)
        .values([{"page_id": pid} for pid in ids])
        .on_conflict_do_update(
            index_elements=[AiIndexJob.page_id],
            set_={"attempts": 0, "last_error": None, "enqueued_at": AiIndexJob.enqueued_at},
        )
    )
    if has_app_context():
        from ymer.ai import get_ai
        from ymer.app import settings_for

        ai = current_app.extensions.get("ymer_ai")
        if ai is not None and ai.embedder is not None and settings_for(current_app).ai_index_inline:
            session.flush()
            for pid in ids:
                _run_job(session, pid, get_ai().embedder)


def _run_job(session: Session, page_id: uuid.UUID, embedder: Embedder) -> bool:
    """Index one queued page inside a savepoint. True if it is done (or gone)."""
    page = session.get(Page, page_id)
    if page is None:
        return True
    try:
        with session.begin_nested():
            index_page(session, page, embedder)
            session.execute(delete(AiIndexJob).where(AiIndexJob.page_id == page_id))
        return True
    except AiError as exc:
        job = session.get(AiIndexJob, page_id)
        if job is not None:
            job.attempts += 1
            job.last_error = str(exc)[:1000]
        log.warning("Indexing page %s failed: %s", page_id, exc)
        return False


def process_queue(session: Session, embedder: Embedder, limit: int = 20) -> int:
    """Index up to ``limit`` queued pages, oldest first. Returns how many were done.

    Rows are locked with SKIP LOCKED, so several workers can share the queue.
    """
    ids = list(
        session.scalars(
            select(AiIndexJob.page_id)
            .where(AiIndexJob.attempts < MAX_ATTEMPTS)
            .order_by(AiIndexJob.enqueued_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    done = 0
    for page_id in ids:
        done += _run_job(session, page_id, embedder)
    session.commit()
    return done


def enqueue_workspace(session: Session, workspace_id: uuid.UUID | None = None) -> int:
    """Queue every page (of one workspace, or all). Returns the number queued."""
    query = select(Page.id)
    if workspace_id is not None:
        query = query.where(Page.workspace_id == workspace_id)
    ids = list(session.scalars(query))
    if ids:
        session.execute(
            insert(AiIndexJob).values([{"page_id": pid} for pid in ids]).on_conflict_do_nothing()
        )
    return len(ids)
