"""Tables for the Factory: turning a workspace's notes into a working app, in stages.

A factory is a page of type ``factory``. Everything a person reads or edits along the
way is an ordinary page under it (the brief is a document), so these tables only keep
the bookkeeping:

- ``factory_projects``: one row per factory page: its stage, its current brief and, from
  stage F2, its stack and the architecture, system map and task pages.
- ``factory_sources``: the pages the factory works from. Reads always go through the
  reader's own access, so a source someone cannot open is left out for them.
- ``factory_questions``: gaps the AI found in the sources, and the answers people gave.
- ``factory_runs`` / ``factory_run_events``: build runs and their step-by-step log.
- ``factory_files``: the code the build wrote, one row per file (the current tree).
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ymer.db import Base
from ymer.models.base import TimestampMixin, uuid_pk


class FactoryStage(enum.StrEnum):
    SOURCES = "sources"  # choosing pages; no brief yet
    BRIEF = "brief"  # a brief exists and is open for questions and edits
    APPROVED = "approved"  # an editor approved the brief
    ARCHITECTURE = "architecture"  # an architecture was designed from the approved brief
    PLANNED = "planned"  # an editor approved the architecture and its tasks
    BUILT = "built"  # a build run finished every task with passing tests


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
    # Stage F2: the architecture, designed from the approved brief.
    stack: Mapped[str | None] = mapped_column(String(40))
    architecture_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="SET NULL")
    )
    map_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="SET NULL")
    )
    tasks_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="SET NULL")
    )
    architecture_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
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


class RunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    STOPPING = "stopping"  # someone asked it to stop; it stops after the current step
    PASSED = "passed"  # every task done, tests pass
    FAILED = "failed"
    STOPPED = "stopped"


ACTIVE_RUN_STATUSES = (RunStatus.QUEUED.value, RunStatus.RUNNING.value, RunStatus.STOPPING.value)


class FactoryRun(Base):
    __tablename__ = "factory_runs"
    __table_args__ = (
        Index("ix_factory_runs_factory_id", "factory_id"),
        Index("ix_factory_runs_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    factory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factory_projects.page_id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RunStatus.QUEUED.value)
    started_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # What the run works from, captured with the starter's access when it was queued:
    # stack, brief and architecture text, and the tasks.
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tasks_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tasks_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    # Set in Python so runs started within one transaction still sort in order.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FactoryRunEvent(Base):
    __tablename__ = "factory_run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_factory_run_events_run_seq"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factory_runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # task, tool, tests, note, error, done
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FactoryFile(Base):
    __tablename__ = "factory_files"
    __table_args__ = (PrimaryKeyConstraint("factory_id", "path"),)

    factory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factory_projects.page_id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factory_runs.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
