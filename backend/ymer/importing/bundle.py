"""Reading an import (a zip, or loose files) and planning the pages it becomes.

Folders become the page tree, the way Notion's "Markdown & CSV" export, an Obsidian
vault or a plain folder of notes lays it out:

- ``Name.md`` is a document; ``Name.csv`` is a database; ``Name.mindmap.json`` is a mind
  map (templates use that one).
- A folder next to a file with the same name (``Name/``) holds that page's sub-pages.
  Any other folder becomes a folder page, if it holds anything importable.
- Notion adds a 32-character id to every name (``Name 1f2e…``). It is left out of titles
  and used to resolve ``notion.so`` links. Notion writes each database twice (``Name.csv``
  and ``Name_all.csv``); the ``_all`` one, which has every row, is used. Database rows
  are exported as pages too; those with more than their properties (a body, or
  sub-pages) are kept, as documents inside the database page.
- Everything else (images, PDFs, …) is an asset: imported when a page links to it.

Nothing here touches the database; :mod:`ymer.importing.run` does that.
"""

import io
import json
import posixpath
import re
import uuid
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import unquote

from ymer.importing import markdown
from ymer.importing.tables import csv_spec
from ymer.models import PageType

MAX_FILES = 3000
MAX_PAGES = 1000
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_TEXT_BYTES = 5 * 1024 * 1024

DOC_EXTENSIONS = (".md", ".markdown")
_NOTION_ID = re.compile(r"\s+([0-9a-f]{32})$")
_PROPERTY_LINE = re.compile(r"^([^:\n]{1,200}):\s?(.*)$")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_NOTION_URL = re.compile(r"notion\.(?:so|site)/\S*?([0-9a-f]{32})", re.IGNORECASE)


class ImportProblem(ValueError):
    """The upload cannot be imported; the message says why, for the person."""


@dataclass
class SourceFile:
    path: str
    data: bytes


@dataclass
class PlanNode:
    title: str
    kind: PageType
    path: str | None = None
    text: str = ""  # a document's Markdown (front matter and title removed)
    spec: dict | None = None  # a database's or mind map's content
    children: list["PlanNode"] = field(default_factory=list)
    id: uuid.UUID = field(default_factory=uuid.uuid4)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class Plan:
    roots: list[PlanNode]
    by_path: dict[str, PlanNode]
    by_notion_id: dict[str, PlanNode]
    by_title: dict[str, PlanNode]
    assets: dict[str, SourceFile]
    skipped: list[str] = field(default_factory=list)

    def nodes(self):
        for root in self.roots:
            yield from root.walk()


# --- reading uploads ------------------------------------------------------------------


def _clean_path(name: str) -> str | None:
    name = name.replace("\\", "/")
    if ".." in name.split("/"):
        return None  # never written to disk, but a path climbing out is not ours to take
    path = posixpath.normpath("/" + name).lstrip("/")
    parts = path.split("/")
    if not path or path == "." or any(p in ("", "..") for p in parts):
        return None
    if any(p.startswith(".") or p == "__MACOSX" for p in parts):
        return None
    return path


class _Reader:
    def __init__(self) -> None:
        self.files: list[SourceFile] = []
        self.total = 0

    def add(self, name: str, data: bytes, *, depth: int = 0) -> None:
        if name.lower().endswith(".zip"):
            if depth > 1:
                return
            self.unzip(data, depth=depth + 1)
            return
        path = _clean_path(name)
        if path is None:
            return
        if len(self.files) >= MAX_FILES:
            raise ImportProblem(f"That import has more than {MAX_FILES:,} files.")
        self.total += len(data)
        if self.total > MAX_TOTAL_BYTES:
            raise ImportProblem(
                f"That import is larger than {MAX_TOTAL_BYTES // (1024 * 1024)} MB unpacked."
            )
        self.files.append(SourceFile(path, data))

    def unzip(self, data: bytes, *, depth: int) -> None:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ImportProblem("That zip file could not be opened.") from exc
        with archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if self.total + info.file_size > MAX_TOTAL_BYTES:
                    raise ImportProblem(
                        "That import is larger than "
                        f"{MAX_TOTAL_BYTES // (1024 * 1024)} MB unpacked."
                    )
                with archive.open(info) as handle:
                    # Read at most one byte more than allowed, whatever the header claims.
                    content = handle.read(MAX_TOTAL_BYTES - self.total + 1)
                self.add(info.filename, content, depth=depth)


def read_uploads(uploads: list[tuple[str, bytes]]) -> list[SourceFile]:
    """Unpack the uploaded files (zips, one level of nested zips) into source files."""
    reader = _Reader()
    for name, data in uploads:
        reader.add(name, data)
    return reader.files


# --- planning -------------------------------------------------------------------------


def display_name(stem: str) -> tuple[str, str | None]:
    """A page title for a file or folder name, and its Notion id if it has one."""
    match = _NOTION_ID.search(stem)
    if match:
        return stem[: match.start()].strip() or stem, match.group(1)
    return stem.strip(), None


def _kind_and_stem(filename: str) -> tuple[PageType, str] | None:
    lower = filename.lower()
    if lower.endswith(".mindmap.json"):
        return PageType.MINDMAP, filename[: -len(".mindmap.json")]
    for ext in DOC_EXTENSIONS:
        if lower.endswith(ext):
            return PageType.DOCUMENT, filename[: -len(ext)]
    if lower.endswith(".csv"):
        return PageType.DATABASE, filename[:-4]
    return None


def _text(file: SourceFile) -> str:
    if len(file.data) > MAX_TEXT_BYTES:
        raise ImportProblem(f"{file.path} is larger than {MAX_TEXT_BYTES // (1024 * 1024)} MB.")
    return file.data.decode("utf-8", errors="replace")


def _strip_properties(text: str, names: set[str]) -> str:
    """Drop the ``Property: value`` lines Notion puts at the top of a database row page."""
    lines = text.lstrip("\n").split("\n")
    i = 0
    while i < len(lines):
        match = _PROPERTY_LINE.match(lines[i])
        if not match or match.group(1).strip().lower() not in names:
            break
        i += 1
    return "\n".join(lines[i:])


def _natural(title: str) -> list:
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", title)]


class _Planner:
    def __init__(self, files: list[SourceFile], substitutions: dict[str, str] | None) -> None:
        self.subs = substitutions or {}
        self.files_in: dict[str, list[SourceFile]] = defaultdict(list)
        self.subdirs: dict[str, set[str]] = defaultdict(set)
        for file in files:
            directory = posixpath.dirname(file.path)
            self.files_in[directory].append(file)
            while directory:
                parent = posixpath.dirname(directory)
                self.subdirs[parent].add(directory)
                directory = parent
        self.plan = Plan([], {}, {}, {}, {})
        self.count = 0

    def substitute(self, text: str) -> str:
        for key, value in self.subs.items():
            text = text.replace("{{" + key + "}}", value)
        return text

    def node(self, file: SourceFile, kind: PageType, stem: str) -> PlanNode:
        self.count += 1
        if self.count > MAX_PAGES:
            raise ImportProblem(f"That import has more than {MAX_PAGES:,} pages.")
        title, notion_id = display_name(self.substitute(stem))
        node = PlanNode(title=title[:500], kind=kind, path=file.path)
        if kind is PageType.DOCUMENT:
            prepared = markdown.prepare(self.substitute(_text(file)))
            node.text = prepared.text
            if prepared.title:
                node.title = prepared.title[:500]
        elif kind is PageType.DATABASE:
            directory = posixpath.dirname(file.path)
            schema_file = next(
                (
                    f
                    for f in self.files_in[directory]
                    if posixpath.basename(f.path).lower() == f"{stem}.schema.json".lower()
                ),
                None,
            )
            schema = None
            if schema_file is not None:
                try:
                    schema = json.loads(_text(schema_file))
                except ValueError:
                    self.plan.skipped.append(schema_file.path)
            node.spec = csv_spec(
                self.substitute(_text(file)), schema if isinstance(schema, dict) else None
            )
        else:
            try:
                spec = json.loads(self.substitute(_text(file)))
            except ValueError:
                spec = None
            if not isinstance(spec, dict):
                raise ImportProblem(f"{file.path} is not a valid mind map.")
            node.spec = spec
            node.title = str(spec.get("title") or node.title)[:500]
        self.plan.by_path[file.path] = node
        if notion_id:
            self.plan.by_notion_id[notion_id] = node
        self.plan.by_title.setdefault(title.lower(), node)
        self.plan.by_title.setdefault(posixpath.splitext(file.path)[0].lower(), node)
        return node

    def build(self, directory: str) -> list[PlanNode]:
        files = self.files_in.get(directory, [])
        names = {posixpath.basename(f.path) for f in files}
        nodes: list[PlanNode] = []
        consumed: set[str] = set()
        for file in sorted(files, key=lambda f: f.path):
            filename = posixpath.basename(file.path)
            found = _kind_and_stem(filename)
            if found is None:
                if not filename.lower().endswith(".schema.json"):
                    self.plan.assets[file.path] = file
                continue
            kind, stem = found
            if kind is PageType.DATABASE:
                if stem.endswith("_all"):
                    stem = stem[:-4]
                elif f"{stem}_all.csv" in names:
                    continue  # Notion's partial copy of the same database
            node = self.node(file, kind, stem)
            if kind is PageType.DATABASE:
                # Pages link to the database by its plain name, without _all.
                self.plan.by_path[posixpath.join(directory, f"{stem}.csv")] = node
            sub = posixpath.join(directory, stem) if directory else stem
            if sub in self.subdirs[directory]:
                consumed.add(sub)
                node.children = self.build(sub)
            if kind is PageType.DATABASE:
                self.keep_row_pages(node)
            nodes.append(node)
        for sub in sorted(self.subdirs[directory] - consumed):
            children = self.build(sub)
            if children:
                title, _ = display_name(posixpath.basename(sub))
                self.count += 1
                nodes.append(PlanNode(title=title[:500], kind=PageType.FOLDER, children=children))
        nodes.sort(key=lambda n: _natural(n.title))
        return nodes

    def keep_row_pages(self, node: PlanNode) -> None:
        names = {p["name"].lower() for p in (node.spec or {}).get("properties", [])}
        kept = []
        for child in node.children:
            if child.kind is PageType.DOCUMENT:
                child.text = _strip_properties(child.text, names)
                if not child.text.strip() and not child.children:
                    continue
            kept.append(child)
        node.children = kept

    def order_by_links(self) -> None:
        """Put sub-pages in the order their parent document links to them (Notion's
        order); pages it does not link to follow, by name."""
        for node in list(self.plan.nodes()):
            if node.kind is not PageType.DOCUMENT or len(node.children) < 2 or not node.path:
                continue

            def position(child: PlanNode, text: str = node.text) -> int:
                if not child.path:
                    return len(text)
                name = posixpath.basename(child.path).replace("_all.csv", ".csv")
                spots = [text.find(name), text.find(name.replace(" ", "%20"))]
                spots = [s for s in spots if s >= 0]
                return min(spots) if spots else len(text)

            node.children.sort(key=position)


def build_plan(files: list[SourceFile], substitutions: dict[str, str] | None = None) -> Plan:
    planner = _Planner(files, substitutions)
    planner.plan.roots = planner.build("")
    planner.order_by_links()
    if not planner.plan.roots:
        raise ImportProblem(
            "Nothing to import: add Markdown (.md) or CSV files, or a zip of them "
            "(such as a Notion export)."
        )
    return planner.plan


def resolve_path(plan: Plan, source_path: str, href: str) -> PlanNode | SourceFile | None:
    """What a link in ``source_path`` points at, among the imported files."""
    if href.startswith("wiki:"):
        name = href[5:].strip().lower()
        return plan.by_title.get(name) or plan.by_title.get(name.removesuffix(".md"))
    if match := _NOTION_URL.search(href):
        return plan.by_notion_id.get(match.group(1).lower())
    if _SCHEME.match(href) or href.startswith(("#", "//")):
        return None
    target = unquote(href.split("#", 1)[0].split("?", 1)[0]).strip()
    if not target:
        return None
    path = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), target))
    for candidate in (path, path + ".md"):
        if candidate in plan.by_path:
            return plan.by_path[candidate]
        if candidate in plan.assets:
            return plan.assets[candidate]
    return None
