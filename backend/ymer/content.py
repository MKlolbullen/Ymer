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


# --- outlines (structured text for the AI layer) --------------------------------------


def outline(state: bytes | None, page_type: PageType) -> str:
    """The page as Markdown-like text that keeps its structure.

    Search text (:func:`summarize`) collapses whitespace; the AI layer wants headings,
    list items and paragraphs on their own lines, and a mind map as an indented list.
    Returns "" for an empty or unreadable state.
    """
    if not state:
        return ""
    doc = pycrdt.Doc()
    lines: list[str] = []
    try:
        doc.apply_update(state)
        if page_type is PageType.MINDMAP:
            _mindmap_outline(doc.get(MINDMAP_MAP, type=pycrdt.Map), lines)
        elif page_type is PageType.DRAWING:
            parts: list[str] = []
            _drawing(doc.get(DRAWING_MAP, type=pycrdt.Map), parts)
            lines = [p.strip() for p in parts if p.strip()]
        else:
            _block_outline(doc.get(FRAGMENT, type=pycrdt.XmlFragment), lines, depth=0)
    except Exception:  # noqa: BLE001
        log.warning("Could not decode a page's Yjs state for its outline.")
        return ""
    return "\n".join(line for line in lines if line.strip())[:_MAX_TEXT]


def _inline_text(node) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, pycrdt.XmlText):
            parts.extend(run for run, _ in child.diff() if isinstance(run, str))
        elif isinstance(child, pycrdt.XmlElement):
            if child.tag == MENTION_NODE:
                parts.append(str(dict(child.attributes).get("label") or "page"))
            else:
                parts.append(_inline_text(child))
    return " ".join("".join(parts).split())


def _block_outline(node, lines: list[str], depth: int) -> None:
    for child in node.children:
        if isinstance(child, pycrdt.XmlText):
            text = " ".join("".join(r for r, _ in child.diff() if isinstance(r, str)).split())
            if text:
                lines.append(text)
            continue
        if not isinstance(child, pycrdt.XmlElement):
            continue
        tag = child.tag
        indent = "  " * depth
        if tag == "heading":
            level = int(dict(child.attributes).get("level") or 1)
            lines.append(f"{'#' * max(1, min(level, 6))} {_inline_text(child)}")
        elif tag in {"paragraph", "codeBlock"}:
            if text := _inline_text(child):
                lines.append(f"{indent}{text}")
        elif tag in {"bulletList", "orderedList", "taskList"}:
            _list_outline(child, lines, depth, ordered=tag == "orderedList")
        elif tag == "blockquote":
            inner: list[str] = []
            _block_outline(child, inner, 0)
            lines.extend(f"> {line}" for line in inner)
        elif tag == "horizontalRule":
            lines.append("---")
        elif tag in {"listItem", "taskItem"}:
            _list_item(child, lines, depth, "-")
        else:
            _block_outline(child, lines, depth)


def _list_outline(node, lines: list[str], depth: int, *, ordered: bool) -> None:
    for index, item in enumerate(node.children, start=1):
        if isinstance(item, pycrdt.XmlElement):
            prefix = f"{index}." if ordered else "-"
            if item.tag == "taskItem":
                checked = str(dict(item.attributes).get("checked")).lower() == "true"
                prefix = f"- [{'x' if checked else ' '}]"
            _list_item(item, lines, depth, prefix)


def _list_item(item, lines: list[str], depth: int, prefix: str) -> None:
    indent = "  " * depth
    first = True
    for child in item.children:
        if not isinstance(child, pycrdt.XmlElement):
            continue
        if child.tag in {"bulletList", "orderedList", "taskList"}:
            _list_outline(child, lines, depth + 1, ordered=child.tag == "orderedList")
        elif first:
            lines.append(f"{indent}{prefix} {_inline_text(child)}")
            first = False
        else:
            lines.append(f"{indent}  {_inline_text(child)}")


def _mindmap_outline(nodes: pycrdt.Map, lines: list[str]) -> None:
    plain = {}
    for node_id, raw in nodes.items():
        node = _plain(raw)
        if isinstance(node, dict):
            plain[str(node_id)] = node
    children: dict[str | None, list[tuple[float, str]]] = {}
    for node_id, node in plain.items():
        parent = node.get("parent") if node.get("parent") in plain else None
        if node_id == "root":
            parent = None
        y = node.get("y") if isinstance(node.get("y"), int | float) else 0
        children.setdefault(parent, []).append((y, node_id))

    def walk(parent: str | None, depth: int, seen: set[str]) -> None:
        for _, node_id in sorted(children.get(parent, [])):
            if node_id in seen:
                continue
            seen.add(node_id)
            label = str(plain[node_id].get("label") or "").strip() or "(untitled idea)"
            lines.append(f"{'  ' * depth}- {label}" if depth or parent else f"# {label}")
            walk(node_id, depth + (1 if parent or depth else 0), seen)

    walk(None, 0, set())
