"""Markdown to a Ymer document (TipTap's Yjs storage format).

The document schema is ``frontend/src/editor/extensions.ts``: StarterKit blocks
(headings 1-3, paragraphs, bullet/ordered lists, blockquotes, code blocks, rules, hard
breaks; bold/italic/strike/code/link marks), task lists, images and file cards stored by
file id, and page mentions. Markdown is mapped onto that schema and anything the schema
cannot hold is turned into the nearest thing it can:

- headings 4-6 become level 3
- tables become a monospaced code block, so the columns still line up
- raw HTML is reduced to its text (a Notion ``<aside>`` callout becomes a quote)
- links to other imported pages become page mentions (so backlinks work), links to
  other imported files become file cards, and links that point nowhere stay as text

Marks are stored the way y-prosemirror stores them: as formatting attributes on the
text, named after the mark, whose value is the mark's attributes.
"""

import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pycrdt
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode

from ymer.content import FRAGMENT, MENTION_NODE

_md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])

_TASK = re.compile(r"^\[( |x|X)\]\s+")
_TAG = re.compile(r"<[^>]+>")
_SAFE_SCHEME = re.compile(r"^(https?:|mailto:)", re.IGNORECASE)
_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(\n|\Z)", re.DOTALL)
_WIKI_LINK = re.compile(r"\[\[([^\[\]|#]+)(?:#[^\[\]|]*)?(?:\|([^\[\]]+))?\]\]")


@dataclass
class Mention:
    page_id: str
    label: str


@dataclass
class Asset:
    """A file stored in Ymer: an image node, or a file card for anything else."""

    file_id: str
    filename: str
    size: int
    is_image: bool


# What the importer tells the converter about a link or image target: a page, a stored
# file, or nothing (keep the link only if it is a safe external URL).
Resolved = Mention | Asset | None
Resolver = Callable[[str, str], Resolved]


def _no_resolver(_href: str, _label: str) -> Resolved:
    return None


@dataclass
class Prepared:
    title: str | None
    text: str


def prepare(text: str) -> Prepared:
    """Strip YAML front matter and a leading ``# Title`` (returned as the title), and turn
    Obsidian-style ``[[wiki links]]`` into ordinary links the resolver can look up."""
    text = text.replace("\r\n", "\n").lstrip("﻿")
    title = None
    if match := _FRONT_MATTER.match(text):
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            if key.strip().lower() == "title" and value.strip():
                title = value.strip().strip("\"'")
        text = text[match.end() :]
    stripped = text.lstrip("\n")
    first, _, rest = stripped.partition("\n")
    if first.startswith("# ") and first[2:].strip():
        title = title or first[2:].strip()
        text = rest
    text = _WIKI_LINK.sub(
        lambda m: f"[{(m.group(2) or m.group(1)).strip()}](<wiki:{m.group(1).strip()}>)", text
    )
    return Prepared(title, text)


class _Builder:
    def __init__(self, resolve: Resolver) -> None:
        self.resolve = resolve
        self.lost: set[str] = set()

    # --- inline --------------------------------------------------------------------

    def inline(self, node: SyntaxTreeNode, marks: dict | None = None) -> list:
        """Inline content as a list of items: ``("t", text, marks)``, an element spec
        ``("el", tag, attrs)`` for inline nodes, or ``("block", spec)`` for something
        that has to leave the paragraph (an image or a file card)."""
        marks = marks or {}
        out: list = []
        for child in node.children:
            kind = child.type
            if kind == "text":
                out.append(("t", child.content, marks))
            elif kind == "softbreak":
                out.append(("t", " ", marks))
            elif kind == "hardbreak":
                out.append(("el", "hardBreak", {}))
            elif kind == "code_inline":
                out.append(("t", child.content, {**marks, "code": {}}))
            elif kind == "strong":
                out.extend(self.inline(child, {**marks, "bold": {}}))
            elif kind == "em":
                out.extend(self.inline(child, {**marks, "italic": {}}))
            elif kind == "s":
                out.extend(self.inline(child, {**marks, "strike": {}}))
            elif kind == "link":
                out.extend(self.link(child, marks))
            elif kind == "image":
                out.extend(self.image(child))
            elif kind == "html_inline":
                if re.match(r"<br\s*/?>", child.content, re.IGNORECASE):
                    out.append(("el", "hardBreak", {}))
            else:  # pragma: no cover - other inline tokens carry their text in children
                out.extend(self.inline(child, marks))
        return out

    @staticmethod
    def plain(items: list) -> str:
        return "".join(i[1] for i in items if i[0] == "t")

    def link(self, node: SyntaxTreeNode, marks: dict) -> list:
        href = str(node.attrs.get("href") or "")
        inner = self.inline(node, marks)
        label = self.plain(inner).strip()
        target = self.resolve(href, label)
        if isinstance(target, Mention):
            return [("el", MENTION_NODE, {"id": target.page_id, "label": label or target.label})]
        if isinstance(target, Asset):
            return [("block", self.asset(target, label))]
        if _SAFE_SCHEME.match(href):
            return self.inline(node, {**marks, "link": {"href": href}})
        if href and not href.startswith("#"):
            self.lost.add(href)
        return inner

    def image(self, node: SyntaxTreeNode) -> list:
        src = str(node.attrs.get("src") or "")
        alt = self.plain(self.inline(node)).strip()
        target = self.resolve(src, alt)
        if isinstance(target, Asset):
            return [("block", self.asset(target, alt))]
        if isinstance(target, Mention):
            return [("el", MENTION_NODE, {"id": target.page_id, "label": target.label})]
        if re.match(r"^https?:", src, re.IGNORECASE):
            return [("block", ("image", {"src": src, "alt": alt or None}, []))]
        if src:
            self.lost.add(src)
        return [("t", alt, {})] if alt else []

    @staticmethod
    def asset(asset: Asset, label: str) -> tuple:
        if asset.is_image:
            return ("image", {"fileId": asset.file_id, "alt": label or asset.filename}, [])
        attrs = {"fileId": asset.file_id, "filename": asset.filename, "size": asset.size}
        return ("fileAttachment", attrs, [])

    def textblock(self, tag: str, attrs: dict, items: list) -> list:
        """A paragraph or heading; images and file cards inside it become blocks of
        their own, after the text that came before them."""
        blocks: list = []
        current: list = []
        for item in items:
            if item[0] == "block":
                if any(i[0] != "t" or i[1].strip() for i in current):
                    blocks.append((tag, attrs, current))
                current = []
                if tag == "heading":
                    continue  # no room for pictures in a heading
                blocks.append(item[1])
            elif not current and blocks and item[0] == "t":
                if item[1].lstrip():
                    current.append(("t", item[1].lstrip(), item[2]))
            else:
                current.append(item)
        if current or not blocks:
            blocks.append((tag, attrs, current))
        return blocks

    # --- blocks --------------------------------------------------------------------

    def blocks(self, node: SyntaxTreeNode) -> list:
        out: list = []
        for child in node.children:
            out.extend(self.block(child))
        return out

    def block(self, node: SyntaxTreeNode) -> list:
        kind = node.type
        if kind == "paragraph":
            return self.textblock("paragraph", {}, self.inline(node.children[0]))
        if kind == "heading":
            level = min(int(node.tag[1]), 3)
            return self.textblock("heading", {"level": level}, self.inline(node.children[0]))
        if kind in ("bullet_list", "ordered_list"):
            return [self.list(node)]
        if kind == "blockquote":
            inner = self.blocks(node) or [("paragraph", {}, [])]
            return [("blockquote", {}, inner)]
        if kind in ("fence", "code_block"):
            language = (node.info or "").strip().split(" ")[0] or None
            code = node.content[:-1] if node.content.endswith("\n") else node.content
            return [("codeBlock", {"language": language}, [("t", code, {})] if code else [])]
        if kind == "hr":
            return [("horizontalRule", {}, [])]
        if kind == "table":
            return [self.table(node)]
        if kind == "html_block":
            return self.html_block(node.content)
        return self.blocks(node)  # pragma: no cover - containers we do not know

    def list(self, node: SyntaxTreeNode) -> tuple:
        items = [c for c in node.children if c.type == "list_item"]
        firsts = [self._first_text(i) for i in items]
        is_tasks = node.type == "bullet_list" and items and all(_TASK.match(t) for t in firsts)
        out = []
        for item in items:
            content = self.blocks(item)
            if not content or content[0][0] != "paragraph":
                content.insert(0, ("paragraph", {}, []))
            if is_tasks:
                checked = self._strip_task(content[0])
                out.append(("taskItem", {"checked": checked}, content))
            else:
                out.append(("listItem", {}, content))
        if is_tasks:
            return ("taskList", {}, out)
        if node.type == "ordered_list":
            start = int(node.attrs.get("start") or 1)
            return ("orderedList", {"start": start} if start != 1 else {}, out)
        return ("bulletList", {}, out)

    @staticmethod
    def _first_text(item: SyntaxTreeNode) -> str:
        para = item.children[0] if item.children else None
        if para is None or para.type != "paragraph":
            return ""
        inline = para.children[0]
        first = inline.children[0] if inline.children else None
        return first.content if first is not None and first.type == "text" else ""

    @staticmethod
    def _strip_task(paragraph: tuple) -> bool:
        items = paragraph[2]
        _, text, marks = items[0]
        match = _TASK.match(text)
        items[0] = ("t", text[match.end() :], marks)
        if not items[0][1]:
            items.pop(0)
        return match.group(1).lower() == "x"

    def table(self, node: SyntaxTreeNode) -> tuple:
        rows: list[list[str]] = []
        for section in node.children:
            for tr in section.children:
                rows.append(
                    [
                        self.plain(self.inline(cell.children[0])).strip() if cell.children else ""
                        for cell in tr.children
                    ]
                )
        width = max((len(r) for r in rows), default=0)
        sizes = [max((len(r[i]) for r in rows if i < len(r)), default=0) for i in range(width)]
        lines = []
        for n, row in enumerate(rows):
            lines.append("  ".join(cell.ljust(sizes[i]) for i, cell in enumerate(row)).rstrip())
            if n == 0 and len(rows) > 1:
                lines.append("  ".join("-" * s for s in sizes))
        self.lost.add("table")
        return ("codeBlock", {"language": None}, [("t", "\n".join(lines), {})])

    def html_block(self, raw: str) -> list:
        """Raw HTML is reduced to its text, which is read again as Markdown (Notion puts
        Markdown inside its ``<aside>`` callouts). A callout becomes a quote."""
        is_callout = raw.lstrip().lower().startswith("<aside")
        raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
        text = html.unescape(_TAG.sub("", raw)).strip()
        if not text:
            return []
        inner = self.blocks(SyntaxTreeNode(_md.parse(text)))
        return [("blockquote", {}, inner)] if is_callout and inner else inner


def _write(parent, spec: tuple) -> None:
    """Append ``(tag, attrs, children)`` to an integrated Yjs element or fragment."""
    tag, attrs, children = spec
    clean = {k: v for k, v in attrs.items() if v is not None}
    element = parent.children.append(pycrdt.XmlElement(tag, clean))
    text: pycrdt.XmlText | None = None
    for child in children:
        if child[0] == "t":
            _, run, marks = child
            if not run:
                continue
            if text is None:
                text = element.children.append(pycrdt.XmlText())
            text.insert(len(text), run, dict(marks) if marks else None)
        elif child[0] == "el":
            _, child_tag, child_attrs = child
            element.children.append(pycrdt.XmlElement(child_tag, child_attrs))
            text = None
        else:
            _write(element, child)
            text = None


@dataclass
class Converted:
    state: bytes
    lost: set[str]


def markdown_to_state(text: str, resolve: Resolver | None = None) -> Converted:
    """Convert prepared Markdown (see :func:`prepare`) to a Yjs update."""
    builder = _Builder(resolve or _no_resolver)
    tree = SyntaxTreeNode(_md.parse(text))
    blocks = builder.blocks(tree)
    doc = pycrdt.Doc()
    fragment = doc.get(FRAGMENT, type=pycrdt.XmlFragment)
    with doc.transaction():
        for spec in blocks:
            _write(fragment, spec)
    return Converted(doc.get_update(), builder.lost)


def blocks_preview(state: bytes) -> list[dict[str, Any]]:
    """The top-level blocks of a document as ``{tag, attrs, text}`` (used by tests)."""
    doc = pycrdt.Doc()
    doc.apply_update(state)
    fragment = doc.get(FRAGMENT, type=pycrdt.XmlFragment)
    return [
        {"tag": child.tag, "attrs": dict(child.attributes), "xml": str(child)}
        for child in fragment.children
        if isinstance(child, pycrdt.XmlElement)
    ]
