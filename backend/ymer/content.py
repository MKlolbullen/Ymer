"""Reading what a live page says, from its Yjs state.

The collab service saves documents, mind maps and drawings as binary Yjs updates. On
every save Flask decodes the update with ``pycrdt`` (the Python binding of the Rust Yjs
port) and pulls out:

- the plain text, for full-text search (``pages.content_text``)
- the pages it mentions, for backlinks (``page_links``)

Where each page type keeps its content (this is the storage format; the frontend
writes it):

- Document: TipTap's XML fragment ``default``. A page mention is an inline
  ``pageMention`` element whose ``id`` attribute is the target page id (see
  ``frontend/src/editor/mentions.tsx``).
- Mind map: the map ``mindmap``, node id -> map ``{label, parent, x, y, color, pageId}``
  (``frontend/src/mindmap/model.ts``). ``pageId`` links a node to a page.
- Drawing: the map ``drawing``, element id -> Excalidraw element JSON
  (``frontend/src/drawing/sync.ts``). Text is in ``text`` elements; deleted elements
  stay with ``isDeleted: true``.
"""

import contextlib
import logging
import uuid
from dataclasses import dataclass, field

import pycrdt
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ymer.models import Page, PageLink, PageType

log = logging.getLogger(__name__)

FRAGMENT = "default"
MENTION_NODE = "pageMention"
MINDMAP_MAP = "mindmap"
DRAWING_MAP = "drawing"
_MAX_TEXT = 1_000_000


@dataclass
class DocumentSummary:
    text: str = ""
    mentions: set[uuid.UUID] = field(default_factory=set)


def summarize(state: bytes, page_type: PageType = PageType.DOCUMENT) -> DocumentSummary | None:
    """Text and mentioned page ids of a live page; None if the state cannot be read."""
    doc = pycrdt.Doc()
    summary = DocumentSummary()
    parts: list[str] = []
    try:
        doc.apply_update(state)
        if page_type is PageType.MINDMAP:
            _mindmap(doc.get(MINDMAP_MAP, type=pycrdt.Map), parts, summary.mentions)
        elif page_type is PageType.DRAWING:
            _drawing(doc.get(DRAWING_MAP, type=pycrdt.Map), parts)
        else:
            _walk(doc.get(FRAGMENT, type=pycrdt.XmlFragment), parts, summary.mentions)
    except Exception:  # noqa: BLE001 - any undecodable state is treated alike
        log.warning("Could not decode a page's Yjs state; skipping text and links.")
        return None
    summary.text = " ".join("".join(parts).split())[:_MAX_TEXT] if parts else ""
    return summary


def _plain(value):
    return value.to_py() if hasattr(value, "to_py") else value


def _mindmap(nodes: pycrdt.Map, parts: list[str], mentions: set[uuid.UUID]) -> None:
    """Node labels, root first then by position, and the pages nodes link to."""
    items = []
    for node_id, raw in nodes.items():
        node = _plain(raw)
        if not isinstance(node, dict):
            continue
        is_root = node.get("parent") in (None, "")
        y = node.get("y") if isinstance(node.get("y"), int | float) else 0
        items.append((0 if is_root else 1, y, str(node_id), node))
    for *_, node in sorted(items, key=lambda item: item[:3]):
        if isinstance(label := node.get("label"), str):
            parts.append(f"{label}\n")
        if page_id := node.get("pageId"):
            with contextlib.suppress(ValueError):
                mentions.add(uuid.UUID(str(page_id)))


def _drawing(elements: pycrdt.Map, parts: list[str]) -> None:
    """Text of the live text elements, top to bottom."""
    texts = []
    for raw in elements.values():
        element = _plain(raw)
        if not isinstance(element, dict) or element.get("isDeleted"):
            continue
        text = element.get("text")
        if isinstance(text, str) and text.strip():
            y = element.get("y") if isinstance(element.get("y"), int | float) else 0
            x = element.get("x") if isinstance(element.get("x"), int | float) else 0
            texts.append((y, x, text))
    parts.extend(f"{text}\n" for *_, text in sorted(texts, key=lambda t: t[:2]))


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
    """Refresh a live page's search text and outgoing links from its new state.

    Only links to pages in the same workspace are kept. The caller commits.
    """
    summary = summarize(state, page.type)
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
