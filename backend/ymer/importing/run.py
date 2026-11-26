"""Creating the pages a :class:`~ymer.importing.bundle.Plan` describes.

Used by imports and by templates. Everything happens in the caller's transaction: pages
first (so links between them can be written as page mentions), then each page's
content. Files that pages link to are stored in S3 and attached to the page that uses
them, as if the person had uploaded them there.
"""

import io
import mimetypes
import posixpath
import uuid
from dataclasses import dataclass, field

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ymer.ai.generate import create_database, mindmap_state
from ymer.ai.indexing import mark_dirty
from ymer.content import index_document
from ymer.databases import relation_target_id
from ymer.documents import save_state
from ymer.importing.bundle import Plan, PlanNode, SourceFile, resolve_path
from ymer.importing.markdown import Asset, Mention, Resolved, markdown_to_state
from ymer.models import (
    DatabaseProperty,
    DatabaseRelation,
    DatabaseRow,
    File,
    Page,
    PageType,
    PropertyType,
)

MAX_DATABASE_PROPERTIES = 40
MAX_DATABASE_ROWS = 5000
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"}


@dataclass
class Target:
    workspace_id: uuid.UUID
    parent_id: uuid.UUID | None
    user_id: uuid.UUID
    version_interval: int
    max_file_bytes: int
    s3: object | None  # boto3 client; None skips files
    bucket: str


@dataclass
class Result:
    roots: list[Page] = field(default_factory=list)
    pages: int = 0
    databases: int = 0
    rows: int = 0
    files: int = 0
    relations: int = 0
    links: int = 0
    lookups: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "pages": self.pages,
            "databases": self.databases,
            "rows": self.rows,
            "files": self.files,
            "relations": self.relations,
            "links": self.links,
            "lookups": self.lookups,
            "warnings": self.warnings[:50],
        }


def _next_position(session: Session, target: Target) -> int:
    parent = (
        Page.parent_id.is_(None) if target.parent_id is None else Page.parent_id == target.parent_id
    )
    current = session.scalar(
        select(func.max(Page.position)).where(Page.workspace_id == target.workspace_id, parent)
    )
    return (current or 0) + 1


class _Runner:
    def __init__(self, session: Session, plan: Plan, target: Target) -> None:
        self.session = session
        self.plan = plan
        self.target = target
        self.result = Result()
        self.pages: dict[uuid.UUID, Page] = {}
        self.uploaded: dict[tuple[uuid.UUID, str], Asset] = {}
        self.lost_links: set[str] = set()
        self.tables = 0

    def create_pages(self, nodes: list[PlanNode], parent_id: uuid.UUID | None, start: int):
        for i, node in enumerate(nodes):
            page = Page(
                id=node.id,
                workspace_id=self.target.workspace_id,
                parent_id=parent_id,
                type=node.kind,
                title=node.title,
                position=start + i,
                created_by_id=self.target.user_id,
            )
            self.session.add(page)
            self.pages[node.id] = page
            self.result.pages += 1
            if parent_id == self.target.parent_id:
                self.result.roots.append(page)
            self.create_pages(node.children, node.id, 0)

    def upload(self, page_id: uuid.UUID, file: SourceFile) -> Asset | None:
        key = (page_id, file.path)
        if key in self.uploaded:
            return self.uploaded[key]
        name = posixpath.basename(file.path)
        if self.target.s3 is None:
            return None
        if len(file.data) > self.target.max_file_bytes:
            self.result.warnings.append(f"{name} is too large to attach, so it was left out.")
            return None
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        file_id = uuid.uuid4()
        stored = File(
            id=file_id,
            workspace_id=self.target.workspace_id,
            page_id=page_id,
            storage_key=f"workspaces/{self.target.workspace_id}/files/{file_id}",
            filename=name[:255],
            content_type=content_type,
            size_bytes=len(file.data),
            uploaded_by_id=self.target.user_id,
        )
        try:
            self.target.s3.upload_fileobj(  # type: ignore[attr-defined]
                io.BytesIO(file.data),
                self.target.bucket,
                stored.storage_key,
                ExtraArgs={"ContentType": content_type},
            )
        except (BotoCoreError, ClientError):
            self.result.warnings.append(
                f"{name} could not be stored (file storage is not reachable), so it was left out."
            )
            self.target.s3 = None  # do not wait on every other file too
            return None
        self.session.add(stored)
        self.result.files += 1
        asset = Asset(str(file_id), stored.filename, stored.size_bytes, content_type in IMAGE_TYPES)
        self.uploaded[key] = asset
        return asset

    def document(self, node: PlanNode) -> None:
        def resolve(href: str, _label: str) -> Resolved:
            found = resolve_path(self.plan, node.path or "", href)
            if isinstance(found, PlanNode):
                return Mention(str(found.id), found.title)
            if isinstance(found, SourceFile):
                return self.upload(node.id, found)
            return None

        converted = markdown_to_state(node.text, resolve)
        if "table" in converted.lost:
            self.tables += 1
        self.lost_links |= {
            h for h in converted.lost if h != "table" and not h.startswith(("http", "mailto"))
        }
        self.save(node, converted.state)

    def save(self, node: PlanNode, state: bytes) -> None:
        save_state(
            self.session,
            node.id,
            state,
            user_id=self.target.user_id,
            version_interval=self.target.version_interval,
        )
        index_document(self.session, self.pages[node.id], state)

    def run(self) -> Result:
        start = _next_position(self.session, self.target)
        self.create_pages(self.plan.roots, self.target.parent_id, start)
        self.session.flush()
        for node in self.plan.nodes():
            if node.kind is PageType.DOCUMENT:
                self.document(node)
            elif node.kind is PageType.DATABASE:
                _, rows = create_database(
                    self.session,
                    self.pages[node.id],
                    node.spec or {},
                    user_id=self.target.user_id,
                    max_properties=MAX_DATABASE_PROPERTIES,
                    max_rows=MAX_DATABASE_ROWS,
                )
                self.result.databases += 1
                self.result.rows += rows
            elif node.kind is PageType.MINDMAP:
                spec = dict(node.spec or {})
                spec.setdefault("title", node.title)
                state, _ = mindmap_state(spec)
                self.save(node, state)
        linked = [n for n in self.plan.nodes() if n.spec and n.spec.get("relations") is not None]
        if linked:
            self.link_databases(linked)
        mark_dirty(self.session, list(self.pages))
        if self.tables:
            self.result.warnings.append(
                f"{self.tables} page(s) had tables; they were kept as text blocks, "
                "since documents do not have tables yet."
            )
        if self.lost_links:
            sample = ", ".join(sorted(self.lost_links)[:5])
            self.result.warnings.append(
                f"{len(self.lost_links)} link(s) point to files that were not in the import "
                f"and were kept as plain text ({sample})."
            )
        unused = [p for p in self.plan.assets if not any(p == k[1] for k in self.uploaded)]
        if unused and self.target.s3 is not None:
            self.result.warnings.append(
                f"{len(unused)} file(s) were not linked from any page, so they were skipped."
            )
        return self.result

    # --- related tables (ymer.importing.relational) ----------------------------------

    def link_databases(self, nodes: list[PlanNode]) -> None:
        self.session.flush()
        props = {n.id: self._props(n.id) for n in nodes}
        rows = {
            n.id: list(
                self.session.scalars(
                    select(DatabaseRow.id)
                    .where(DatabaseRow.database_id == n.id)
                    .order_by(DatabaseRow.position)
                )
            )
            for n in nodes
        }
        # Each table's key -> row id, for the tables others point at.
        by_key = {
            n.id: {
                key: rows[n.id][i] for i, key in enumerate(n.spec["keys"]) if i < len(rows[n.id])
            }
            for n in nodes
        }
        missing = 0
        for node in nodes:
            for spec in node.spec["relations"]:
                target = uuid.UUID(spec["target"])
                if target not in by_key:
                    continue
                prop = self._relation(node, target, spec, props)
                for i, cell in enumerate(spec["cells"]):
                    if i >= len(rows[node.id]) or not cell:
                        continue
                    seen: set[uuid.UUID] = set()
                    for position, key in enumerate(p.strip() for p in cell.split(",")):
                        other = by_key[target].get(key)
                        if other is None:
                            missing += 1
                            continue
                        if other in seen:
                            continue
                        seen.add(other)
                        self.session.add(
                            DatabaseRelation(
                                property_id=prop.id,
                                from_row_id=rows[node.id][i],
                                to_row_id=other,
                                position=position,
                            )
                        )
                        self.result.links += 1
        self._lookups(nodes, props)
        for node in nodes:
            order = {name.lower(): i for i, name in enumerate(node.spec.get("order", []))}
            ordered = sorted(
                props[node.id],
                key=lambda p, o=order: (
                    p.type is not PropertyType.TITLE,
                    o.get(p.name.lower(), 999),
                ),
            )
            for i, prop in enumerate(ordered):
                prop.position = i
        if missing:
            self.result.warnings.append(
                f"{missing} id(s) in the imported tables point at rows that are not there, "
                "so those links were left out."
            )

    def _props(self, database_id: uuid.UUID) -> list[DatabaseProperty]:
        return list(
            self.session.scalars(
                select(DatabaseProperty).where(DatabaseProperty.database_id == database_id)
            )
        )

    def _relation(
        self,
        node: PlanNode,
        target: uuid.UUID,
        spec: dict,
        props: dict[uuid.UUID, list[DatabaseProperty]],
    ) -> DatabaseProperty:
        prop = DatabaseProperty(
            id=uuid.uuid4(),
            workspace_id=self.target.workspace_id,
            database_id=node.id,
            name=_unique(spec["name"], props[node.id]),
            type=PropertyType.RELATION,
            config={"database_id": str(target)},
            position=len(props[node.id]),
        )
        props[node.id].append(prop)
        self.session.add(prop)
        if spec.get("reverse_name") and target != node.id:
            reverse = DatabaseProperty(
                id=uuid.uuid4(),
                workspace_id=self.target.workspace_id,
                database_id=target,
                name=_unique(spec["reverse_name"], props[target]),
                type=PropertyType.RELATION,
                config={"database_id": str(node.id), "reverse_of": str(prop.id)},
                position=len(props[target]) + 100,  # after the table's own columns
            )
            prop.config = {**prop.config, "reverse_property_id": str(reverse.id)}
            props[target].append(reverse)
            self.session.add(reverse)
        self.session.flush()
        self.result.relations += 1
        return prop

    def _lookups(self, nodes: list[PlanNode], props: dict[uuid.UUID, list[DatabaseProperty]]):
        """Lookups are rollups showing the related row's value. One can show another
        lookup, so they are made in rounds until no more can be."""
        waiting = [(n, spec) for n in nodes for spec in n.spec.get("lookups", [])]
        while waiting:
            later = []
            for node, spec in waiting:
                relation = _named(props[node.id], spec["relation"], PropertyType.RELATION)
                target_db = relation_target_id(relation) if relation else None
                shown = (
                    _named(props.get(target_db, []), spec["target_property"]) if target_db else None
                )
                if relation is None or target_db is None:
                    continue
                if shown is None:
                    later.append((node, spec))
                    continue
                lookup = DatabaseProperty(
                    workspace_id=self.target.workspace_id,
                    database_id=node.id,
                    name=_unique(spec["name"], props[node.id]),
                    type=PropertyType.ROLLUP,
                    config={
                        "relation_property_id": str(relation.id),
                        "target_property_id": str(shown.id),
                        "function": "show_original",
                    },
                    position=len(props[node.id]),
                )
                props[node.id].append(lookup)
                self.session.add(lookup)
                self.result.lookups += 1
            if len(later) == len(waiting):
                break  # what is left points at columns that do not exist
            waiting = later
        self.session.flush()


def _named(
    props: list[DatabaseProperty], name: str, kind: PropertyType | None = None
) -> DatabaseProperty | None:
    return next(
        (p for p in props if p.name.lower() == name.lower() and (kind is None or p.type is kind)),
        None,
    )


def _unique(name: str, props: list[DatabaseProperty]) -> str:
    taken = {p.name.lower() for p in props}
    candidate = name[:200]
    n = 2
    while candidate.lower() in taken:
        candidate = f"{name[:190]} ({n})"
        n += 1
    return candidate


def run_plan(session: Session, plan: Plan, target: Target) -> Result:
    return _Runner(session, plan, target).run()
