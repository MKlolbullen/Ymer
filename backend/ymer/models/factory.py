"""Tables for the Factory: turning a workspace's notes into a working app, in stages.

A factory is a page of type ``factory``. Everything a person reads or edits along the
way is an ordinary page under it (the brief is a document), so these tables only keep
the bookkeeping:

- ``factory_projects``: one row per factory page: its stage and its current brief.
- ``factory_sources``: the pages the factory works from. Reads always go through the
  reader's own access, so a source someone cannot open is left out for them.
- ``factory_questions``: gaps the AI found in the sources, and the answers people gave.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ymer.db import Base
from ymer.models.base import TimestampMixin, uuid_pk


class FactoryStage(enum.StrEnum):
    SOURCES = "sources"  # choosing pages; no brief yet
    BRIEF = "brief"  # a brief exists and is open for questions and edits
    APPROVED = "approved"  # an editor approved the brief


class QuestionStatus(enum.StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    DISMISSED = "dismissed"


class FactoryProject(TimestampMixin, Base):
    __tablename__ = "factory_projects"

    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), primary_key=True
    )
    stage: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FactoryStage.SOURCES.value
    )
    brief_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="SET NULL")
    )
    brief_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )


class FactorySource(Base):
    __tablename__ = "factory_sources"
    __table_args__ = (
        UniqueConstraint("factory_id", "page_id", name="uq_factory_sources_factory_page"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    factory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factory_projects.page_id", ondelete="CASCADE"),
        nullable=False,
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FactoryQuestion(Base):
    __tablename__ = "factory_questions"
    __table_args__ = (Index("ix_factory_questions_factory_id", "factory_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    factory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factory_projects.page_id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Why the AI asks: what is missing or unclear in the sources.
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=QuestionStatus.OPEN.value
    )
    answer: Mapped[str | None] = mapped_column(Text)
    answered_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
