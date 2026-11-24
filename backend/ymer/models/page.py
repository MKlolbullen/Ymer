import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ymer.db import Base
from ymer.models.base import TimestampMixin, uuid_pk
from ymer.models.role import Role
from ymer.models.workspace import role_enum


class PageType(enum.StrEnum):
    DOCUMENT = "document"
    DATABASE = "database"
    MINDMAP = "mindmap"
    FOLDER = "folder"


class Page(TimestampMixin, Base):
    """A node in a workspace's page tree. Content lives in type-specific tables later."""

    __tablename__ = "pages"
    __table_args__ = (
        # Target for the composite parent FK below.
        UniqueConstraint("id", "workspace_id", name="uq_pages_id_workspace_id"),
        # A parent must be in the same workspace as its child.
        ForeignKeyConstraint(
            ["parent_id", "workspace_id"],
            ["pages.id", "pages.workspace_id"],
            name="fk_pages_parent_same_workspace",
            ondelete="CASCADE",
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
        Index("ix_pages_workspace_id_parent_id", "workspace_id", "parent_id"),
        Index("ix_pages_parent_id", "parent_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    type: Mapped[PageType] = mapped_column(
        Enum(
            PageType,
            name="page_type",
            values_callable=lambda types: [t.value for t in types],
            validate_strings=True,
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(500), nullable=False, default="", server_default=text("''")
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class PagePermission(Base):
    """An explicit grant of a role on a page (and its descendants) to one user.

    The nearest grant on a page or its ancestors overrides the user's workspace role,
    so it can widen access (share with a guest) or narrow it (make a page view-only).
    Workspace owners are never narrowed.
    """

    __tablename__ = "page_permissions"
    __table_args__ = (
        UniqueConstraint("page_id", "user_id"),
        CheckConstraint("role <> 'owner'", name="role_not_owner"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[Role] = mapped_column(role_enum, nullable=False)
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
