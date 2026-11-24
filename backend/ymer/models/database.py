"""Notion-style databases.

A database is a page of type ``database``. Its schema is a list of properties, its
records are rows whose values live in one JSONB object keyed by property id, and its
saved ways of looking at the rows are views (table or board, with filters and sorts).

Relation values are not kept in the JSONB: each link between two rows is a row in
``database_relations``, so links stay consistent when a row is deleted and can be read
from either side (two-way relations). Rollups are computed when rows are read.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Computed,
    DateTime,
    Double,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column

from ymer.db import Base
from ymer.models.base import TimestampMixin, uuid_pk


class PropertyType(enum.StrEnum):
    TITLE = "title"
    TEXT = "text"
    NUMBER = "number"
    SELECT = "select"
    MULTI_SELECT = "multi_select"
    DATE = "date"
    CHECKBOX = "checkbox"
    URL = "url"
    PERSON = "person"
    RELATION = "relation"
    ROLLUP = "rollup"


class ViewType(enum.StrEnum):
    TABLE = "table"
    BOARD = "board"


def _enum(cls: type[enum.StrEnum], name: str) -> Enum:
    return Enum(
        cls,
        name=name,
        values_callable=lambda items: [i.value for i in items],
        validate_strings=True,
    )


def _same_workspace_fk(table: str) -> ForeignKeyConstraint:
    """The database page must be in the same workspace as the row/property/view."""
    return ForeignKeyConstraint(
        ["database_id", "workspace_id"],
        ["pages.id", "pages.workspace_id"],
        name=f"fk_{table}_database_same_workspace",
        ondelete="CASCADE",
    )


class DatabaseProperty(Base):
    """One column of a database.

    ``config`` depends on the type:

    - select / multi_select: ``{"options": [{"id", "name", "color"}]}``
    - number: ``{"format": "number" | "percent" | ...}`` (display only)
    - relation: ``{"database_id": <target page>}`` plus either ``"reverse_property_id"``
      (this side owns the links; the other side is the given property) or ``"reverse_of"``
      (this is the read-from-the-other-side half of a two-way relation)
    - rollup: ``{"relation_property_id", "target_property_id", "function"}``
    """

    __tablename__ = "database_properties"
    __table_args__ = (
        _same_workspace_fk("database_properties"),
        Index("ix_database_properties_database_id", "database_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    database_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[PropertyType] = mapped_column(_enum(PropertyType, "property_type"), nullable=False)
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DatabaseRow(TimestampMixin, Base):
    """One record. ``values`` maps property id (string) to a JSON value for that type."""

    __tablename__ = "database_rows"
    __table_args__ = (
        _same_workspace_fk("database_rows"),
        Index("ix_database_rows_database_id_position", "database_id", "position"),
        Index("ix_database_rows_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    database_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    values: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # Fractional so a row can be dropped between two others without renumbering.
    position: Mapped[float] = mapped_column(
        Double, nullable=False, default=0, server_default=text("0")
    )
    # Title and text values, kept in step with ``values`` for full-text search.
    search_text: Mapped[str] = deferred(
        mapped_column(Text, nullable=False, default="", server_default=text("''"))
    )
    search_vector: Mapped[str] = deferred(
        mapped_column(
            TSVECTOR, Computed("to_tsvector('simple'::regconfig, search_text)", persisted=True)
        )
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class DatabaseRelation(Base):
    """A link from one row to another through a relation property (the owning side)."""

    __tablename__ = "database_relations"
    __table_args__ = (Index("ix_database_relations_to_row_id", "to_row_id"),)

    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_properties.id", ondelete="CASCADE"),
        primary_key=True,
    )
    from_row_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("database_rows.id", ondelete="CASCADE"), primary_key=True
    )
    to_row_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("database_rows.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class DatabaseView(Base):
    """A saved view: ``config`` holds sorts, filters, group-by and hidden properties."""

    __tablename__ = "database_views"
    __table_args__ = (
        _same_workspace_fk("database_views"),
        Index("ix_database_views_database_id", "database_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    database_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[ViewType] = mapped_column(_enum(ViewType, "view_type"), nullable=False)
    config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
