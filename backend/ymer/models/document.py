"""Document content, version snapshots and comments.

The live content of a document page is a Yjs update (binary CRDT state) written by the
collab service (``collab/``) through the internal API. Flask never edits it; it only
stores it, snapshots it into versions, and hands it back.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ymer.db import Base
from ymer.models.base import TimestampMixin, uuid_pk


class DocumentContent(Base):
    """Current Yjs state of one document page (one row per page, created on first save)."""

    __tablename__ = "document_content"

    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DocumentVersion(Base):
    """A saved snapshot of a document's Yjs state.

    Snapshots are taken automatically while people edit (see ``ymer.documents``) and on
    request ("Save version"). ``label`` is set for named versions and null for automatic ones.
    """

    __tablename__ = "document_versions"
    __table_args__ = (Index("ix_document_versions_page_id_created_at", "page_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    label: Mapped[str | None] = mapped_column(String(200))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Comment(TimestampMixin, Base):
    """A comment on a page. Threads are a root comment plus replies (``thread_id`` set).

    A root comment may be anchored to a text range. The anchor holds two Yjs relative
    positions (base64 JSON, made by the client), so it follows the text as others edit,
    and lets commenters anchor without write access to the document itself.
    """

    __tablename__ = "comments"
    __table_args__ = (Index("ix_comments_page_id_created_at", "page_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("comments.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    anchor: Mapped[dict | None] = mapped_column(JSONB)
    quote: Mapped[str | None] = mapped_column(String(1000))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
