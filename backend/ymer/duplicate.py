"""Copying a page and everything beneath it (``Duplicate``, and custom templates).

Only pages the person can see are copied. The copy is a full, independent set of
pages: live content (documents, mind maps, drawings) is copied from its saved state,
databases with their properties, rows, views and relations, and attached files are
copied in S3 so deleting one copy never breaks the other.

Ids that point inside the copied pages are rewritten to the copies: page mentions and
chart blocks in documents, linked pages in mind maps, relation and rollup properties,
and property ids in view settings. Relations to databases outside the copy are left out,
since the other side of a two-way relation cannot be duplicated with it.
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass, field

import pycrdt
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ymer.ai.indexing import mark_dirty
from ymer.content import FRAGMENT, MINDMAP_MAP, index_document
from ymer.databases import search_text_for
from ymer.documents import save_state
from ymer.models import (
    DatabaseProperty,
    DatabaseRelation,
    DatabaseRow,
    DatabaseView,
    DocumentContent,
    FactoryProject,
    File,
    Page,
    PageType,
    PropertyType,
)
from ymer.permissions import WorkspaceScope

log = logging.getLogger(__name__)
MAX_PAGES = 500


class TooLarge(ValueError):
    pass


@dataclass
class Copied:
    root: Page
    pages: int = 0
    files: int = 0
    warnings: list[str] = field(default_factory=list)


def _remap_xml(node, ids: dict[str, str]) -> None:
    for child in node.children:
        if isinstance(child, pycrdt.XmlElement):
            for key, value in list(child.attributes):
                if isinstance(value, str) and value in ids:
                    child.attributes[key] = ids[value]
            _remap_xml(child, ids)


def remap_state(state: bytes, page_type: PageType, ids: dict[str, str]) -> bytes:
    """``state`` with every id in ``ids`` replaced, where pages keep ids (see module doc)."""
    if page_type not in (PageType.DOCUMENT, PageType.MINDMAP):
        return state
    doc = pycrdt.Doc()
    doc.apply_update(state)
    with doc.transaction():
        if page_type is PageType.DOCUMENT:
            _remap_xml(doc.get(FRAGMENT, type=pycrdt.XmlFragment), ids)
        else:
            nodes = doc.get(MINDMAP_MAP, type=pycrdt.Map)
            for node in nodes.values():
                if isinstance(node, pycrdt.Map):
                    for key, value in list(node.items()):
                        if isinstance(value, str) and value in ids:
                            node[key] = ids[value]
    return doc.get_update()


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _remap_json(value: dict, ids: dict[str, str]) -> dict:
    text = _UUID.sub(lambda m: ids.get(m.group(0), m.group(0)), json.dumps(value))
    return json.loads(text)


class _Copier:
    def __init__(
        self,
        session: Session,
        scope: WorkspaceScope,
        *,
        user_id: uuid.UUID,
        version_interval: int,
        s3,
        bucket: str,
    ) -> None:
        self.session = session
        self.scope = scope
        self.user_id = user_id
        self.version_interval = version_interval
        self.s3 = s3
        self.bucket = bucket
        self.ids: dict[str, str] = {}  # old id -> new id, as strings

    def pages(self, root: Page) -> list[Page]:
        ids = self.scope.subtree_ids(root.id)
        if len(ids) > MAX_PAGES:
            raise TooLarge(f"Only pages with up to {MAX_PAGES} pages beneath them can be copied.")
        pages = list(self.session.scalars(select(Page).where(Page.id.in_(ids))))
        by_parent: dict[uuid.UUID | None, list[Page]] = {}
        for page in pages:
            by_parent.setdefault(page.parent_id, []).append(page)
        ordered: list[Page] = []

        def visit(page: Page) -> None:
            if self.scope.page_role(page) is None:
                return  # hidden from this person, with everything beneath it
            ordered.append(page)
            for child in sorted(by_parent.get(page.id, []), key=lambda p: p.position):
                visit(child)

        visit(root)
        return ordered

    def copy(self, root: Page, *, parent_id: uuid.UUID | None, title: str, position: int) -> Copied:
        sources = self.pages(root)
        for page in sources:
            self.ids[str(page.id)] = str(uuid.uuid4())
        new_pages: dict[uuid.UUID, Page] = {}
        for page in sources:
            is_root = page.id == root.id
            copy = Page(
                id=uuid.UUID(self.ids[str(page.id)]),
                workspace_id=page.workspace_id,
                parent_id=parent_id if is_root else uuid.UUID(self.ids[str(page.parent_id)]),
                type=page.type,
                title=title if is_root else page.title,
                position=position if is_root else page.position,
                created_by_id=self.user_id,
            )
            self.session.add(copy)
            new_pages[page.id] = copy
            if page.type is PageType.FACTORY:
                self.session.add(FactoryProject(page_id=copy.id))
        self.session.flush()

        result = Copied(root=new_pages[root.id], pages=len(sources))
        source_ids = [p.id for p in sources]
        result.files = self.copy_files(source_ids, result)
        self.copy_databases([p for p in sources if p.type is PageType.DATABASE])
        contents = self.session.scalars(
            select(DocumentContent).where(DocumentContent.page_id.in_(source_ids))
        )
        by_id = {p.id: p for p in sources}
        for content in contents:
            page = by_id[content.page_id]
            state = remap_state(content.state, page.type, self.ids)
            target = new_pages[page.id]
            save_state(
                self.session,
                target.id,
                state,
                user_id=self.user_id,
                version_interval=self.version_interval,
            )
            index_document(self.session, target, state)
        mark_dirty(self.session, [p.id for p in new_pages.values()])
        return result

    def copy_files(self, page_ids: list[uuid.UUID], result: Copied) -> int:
        files = list(self.session.scalars(select(File).where(File.page_id.in_(page_ids))))
        copied = 0
        for file in files:
            new_id = uuid.uuid4()
            key = f"workspaces/{file.workspace_id}/files/{new_id}"
            try:
                if self.s3 is None:
                    raise ClientError({"Error": {"Code": "NoStorage"}}, "CopyObject")
                self.s3.copy_object(
                    Bucket=self.bucket,
                    Key=key,
                    CopySource={"Bucket": self.bucket, "Key": file.storage_key},
                )
            except (BotoCoreError, ClientError):
                log.exception("Could not copy stored file %s", file.id)
                result.warnings.append(f"{file.filename} could not be copied.")
                continue
            self.ids[str(file.id)] = str(new_id)
            self.session.add(
                File(
                    id=new_id,
                    workspace_id=file.workspace_id,
                    page_id=uuid.UUID(self.ids[str(file.page_id)]),
                    storage_key=key,
                    filename=file.filename,
                    content_type=file.content_type,
                    size_bytes=file.size_bytes,
                    uploaded_by_id=self.user_id,
                )
            )
            copied += 1
        return copied

    def copy_databases(self, databases: list[Page]) -> None:
        if not databases:
            return
        db_ids = [p.id for p in databases]
        props = list(
            self.session.scalars(
                select(DatabaseProperty)
                .where(DatabaseProperty.database_id.in_(db_ids))
                .order_by(DatabaseProperty.position)
            )
        )
        # Relations to databases outside the copy are dropped, and rollups through them.
        dropped: set[str] = set()
        for prop in props:
            target = prop.config.get("database_id")
            if prop.type is PropertyType.RELATION and str(target) not in self.ids:
                dropped.add(str(prop.id))
        for prop in props:
            if prop.type is PropertyType.ROLLUP and (
                str(prop.config.get("relation_property_id")) in dropped
                or str(prop.config.get("through_property_id")) in dropped
            ):
                dropped.add(str(prop.id))
        kept = [p for p in props if str(p.id) not in dropped]
        for prop in kept:
            self.ids[str(prop.id)] = str(uuid.uuid4())
        new_props: dict[uuid.UUID, list[DatabaseProperty]] = {}
        for prop in kept:
            copy = DatabaseProperty(
                id=uuid.UUID(self.ids[str(prop.id)]),
                workspace_id=prop.workspace_id,
                database_id=uuid.UUID(self.ids[str(prop.database_id)]),
                name=prop.name,
                type=prop.type,
                config=_remap_json(prop.config, self.ids),
                position=prop.position,
            )
            self.session.add(copy)
            new_props.setdefault(copy.database_id, []).append(copy)

        rows = list(
            self.session.scalars(select(DatabaseRow).where(DatabaseRow.database_id.in_(db_ids)))
        )
        for row in rows:
            self.ids[str(row.id)] = str(uuid.uuid4())
        for row in rows:
            new_db = uuid.UUID(self.ids[str(row.database_id)])
            # Values can point at files of the page (file properties): those are copies now.
            values = _remap_json(
                {self.ids[k]: v for k, v in row.values.items() if k in self.ids}, self.ids
            )
            self.session.add(
                DatabaseRow(
                    id=uuid.UUID(self.ids[str(row.id)]),
                    workspace_id=row.workspace_id,
                    database_id=new_db,
                    values=values,
                    position=row.position,
                    search_text=search_text_for(new_props.get(new_db, []), values),
                    created_by_id=self.user_id,
                    updated_by_id=self.user_id,
                )
            )
        self.session.flush()

        prop_ids = [p.id for p in kept]
        if prop_ids:
            for link in self.session.scalars(
                select(DatabaseRelation).where(DatabaseRelation.property_id.in_(prop_ids))
            ):
                if str(link.from_row_id) in self.ids and str(link.to_row_id) in self.ids:
                    self.session.add(
                        DatabaseRelation(
                            property_id=uuid.UUID(self.ids[str(link.property_id)]),
                            from_row_id=uuid.UUID(self.ids[str(link.from_row_id)]),
                            to_row_id=uuid.UUID(self.ids[str(link.to_row_id)]),
                            position=link.position,
                        )
                    )
        for view in self.session.scalars(
            select(DatabaseView).where(DatabaseView.database_id.in_(db_ids))
        ):
            self.session.add(
                DatabaseView(
                    workspace_id=view.workspace_id,
                    database_id=uuid.UUID(self.ids[str(view.database_id)]),
                    name=view.name,
                    type=view.type,
                    config=_remap_json(view.config, self.ids),
                    position=view.position,
                )
            )


def copy_page(
    session: Session,
    scope: WorkspaceScope,
    root: Page,
    *,
    parent_id,
    title: str,
    position: int,
    user_id: uuid.UUID,
    version_interval: int,
    s3,
    bucket: str,
) -> Copied:
    copier = _Copier(
        session, scope, user_id=user_id, version_interval=version_interval, s3=s3, bucket=bucket
    )
    return copier.copy(root, parent_id=parent_id, title=title, position=position)
