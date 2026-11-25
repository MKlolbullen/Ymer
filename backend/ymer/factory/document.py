"""Writing a TipTap document as a Yjs state, the way the editor stores it.

Only the block types the brief needs: headings, paragraphs, bullet lists and task lists.
Inline content is plain text or a page mention (``("mention", page_id, label)``), which
the editor shows as a link and Ymer counts as a backlink.
"""

import uuid
from dataclasses import dataclass, field

import pycrdt

from ymer.content import FRAGMENT, MENTION_NODE

Inline = str | tuple[str, uuid.UUID, str]


@dataclass
class Block:
    kind: str  # heading, paragraph, bullets, tasks
    content: list = field(default_factory=list)
    level: int = 2


def heading(text: str, level: int = 2) -> Block:
    return Block("heading", [text], level)


def paragraph(*parts: Inline) -> Block:
    return Block("paragraph", list(parts))


def bullets(items: list[Inline | list[Inline]]) -> Block:
    return Block("bullets", items)


def tasks(items: list[tuple[str, bool]]) -> Block:
    return Block("tasks", items)


def _inline(parts: list[Inline]) -> list:
    out = []
    for part in parts:
        if isinstance(part, tuple):
            _, page_id, label = part
            out.append(pycrdt.XmlElement(MENTION_NODE, {"id": str(page_id), "label": label}))
        elif part:
            out.append(pycrdt.XmlText(part))
    return out


def _paragraph(parts: list[Inline]) -> pycrdt.XmlElement:
    return pycrdt.XmlElement("paragraph", {}, _inline(parts))


def document_state(blocks: list[Block]) -> bytes:
    doc = pycrdt.Doc()
    fragment = doc.get(FRAGMENT, type=pycrdt.XmlFragment)
    for block in blocks:
        if block.kind == "heading":
            node = pycrdt.XmlElement("heading", {"level": block.level}, _inline(block.content))
        elif block.kind == "paragraph":
            node = _paragraph(block.content)
        elif block.kind == "bullets":
            items = [
                pycrdt.XmlElement("listItem", {}, [_paragraph(i if isinstance(i, list) else [i])])
                for i in block.content
            ]
            if not items:
                continue
            node = pycrdt.XmlElement("bulletList", {}, items)
        elif block.kind == "tasks":
            items = [
                pycrdt.XmlElement("taskItem", {"checked": checked}, [_paragraph([text])])
                for text, checked in block.content
            ]
            if not items:
                continue
            node = pycrdt.XmlElement("taskList", {}, items)
        else:  # pragma: no cover - programming error
            raise ValueError(block.kind)
        fragment.children.append(node)
    return doc.get_update()
