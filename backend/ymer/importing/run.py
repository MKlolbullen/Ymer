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
from ymer.documents import save_state
from ymer.importing.bundle import Plan, PlanNode, SourceFile, resolve_path
from ymer.importing.markdown import Asset, Mention, Resolved, markdown_to_state
from ymer.models import File, Page, PageType

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
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "pages": self.pages,
            "databases": self.databases,
            "rows": self.rows,
            "files": self.files,
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


def run_plan(session: Session, plan: Plan, target: Target) -> Result:
    return _Runner(session, plan, target).run()
