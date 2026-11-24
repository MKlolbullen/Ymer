"""Reading what a document says, from its Yjs state.

The collab service saves documents as binary Yjs updates. On every save Flask decodes
the update with ``pycrdt`` (the Python binding of the Rust Yjs port) and pulls out:

- the plain text, for full-text search (``pages.content_text``)
- the pages it mentions, for backlinks (``page_links``)

TipTap keeps the document in the XML fragment named ``default``; a page mention is an
inline ``pageMention`` element whose ``id`` attribute is the target page id (see
``frontend/src/editor/mentions.tsx``).
"""

import contextlib
import logging
import uuid
from dataclasses import dataclass, field

import pycrdt
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ymer.models import Page, PageLink

log = logging.getLogger(__name__)

FRAGMENT = "default"
MENTION_NODE = "pageMention"
_MAX_TEXT = 1_000_000


@dataclass
class DocumentSummary:
    text: str = ""
    mentions: set[uuid.UUID] = field(default_factory=set)


def summarize(state: bytes) -> DocumentSummary | None:
    """Text and mentioned page ids of a Yjs document; None if the state cannot be read."""
    doc = pycrdt.Doc()
    try:
        doc.apply_update(state)
        fragment = doc.get(FRAGMENT, type=pycrdt.XmlFragment)
    except Exception:  # noqa: BLE001 - any undecodable state is treated alike
        log.warning("Could not decode a document's Yjs state; skipping text and links.")
        return None
    summary = DocumentSummary()
    parts: list[str] = []
    _walk(fragment, parts, summary.mentions)
    summary.text = " ".join("".join(parts).split())[:_MAX_TEXT] if parts else ""
    return summary


def _walk(node, parts: list[str], mentions: set[uuid.UUID]) -> None:
    for child in node.children:
        if isinstance(child, pycrdt.XmlText):
            # diff() yields (insert, attributes) runs; str() would include format tags.
            parts.extend(run for run, _ in child.diff() if isinstance(run, str))
        elif isinstance(child, pycrdt.XmlElement):
            if child.tag == MENTION_NODE:
                attrs = dict(child.attributes)
                with contextlib.suppress(ValueError):
                    mentions.add(uuid.UUID(str(attrs.get("id"))))
                if label := attrs.get("label"):
                    parts.append(f" {label} ")
                continue
            _walk(child, parts, mentions)
            parts.append("\n")  # keep words of neighbouring blocks apart


def index_document(session: Session, page: Page, state: bytes) -> None:
    """Refresh a document's search text and outgoing links from its new state.

    Only links to pages in the same workspace are kept. The caller commits.
    """
    summary = summarize(state)
    if summary is None:
        return
    page.content_text = summary.text
    session.execute(delete(PageLink).where(PageLink.source_page_id == page.id))
    targets = (summary.mentions - {page.id}) and set(
        session.scalars(
            select(Page.id).where(
                Page.id.in_(summary.mentions - {page.id}),
                Page.workspace_id == page.workspace_id,
            )
        )
    )
    if targets:
        session.execute(
            insert(PageLink)
            .values([{"source_page_id": page.id, "target_page_id": t} for t in targets])
            .on_conflict_do_nothing()
        )
