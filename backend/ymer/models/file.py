import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ymer.db import Base
from ymer.models.base import uuid_pk


class File(Base):
    """Metadata for an uploaded file; the bytes live in S3 under ``storage_key``.

    Every file belongs to a page, and access follows the page: viewers of the page can
    download it, editors can upload and delete.
    """

    __tablename__ = "files"
    __table_args__ = (
        # The page must be in the same workspace as the file.
        ForeignKeyConstraint(
            ["page_id", "workspace_id"],
            ["pages.id", "pages.workspace_id"],
            name="fk_files_page_same_workspace",
            ondelete="CASCADE",
        ),
        Index("ix_files_page_id", "page_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
