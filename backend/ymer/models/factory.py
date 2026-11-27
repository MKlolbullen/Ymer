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
- ``factory_releases``: builds an editor reviewed and approved, as numbered snapshots of
  the files; what gets downloaded, previewed and pushed.
- ``factory_pushes``: each push of a release to a git repository.
- ``factory_changes``: change requests on a built app, from a review or from a changed
  spec; planning one adds tasks, and a passed build and a release close it.
- ``factory_ci_runs``: the app's checks (lint, tests, build, smoke) run on its files after
  every passed build or on request; a release needs them to pass, and a failure turns
  into a fix task.
"""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
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
    RELEASED = "released"  # an editor approved a build as a release


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
    # Stage F4: where releases were last pushed, offered again next time.
    git_remote: Mapped[str | None] = mapped_column(String(500))
    git_branch: Mapped[str | None] = mapped_column(String(200))
    # Checks (CI): the database page that keeps their history, whether a failure starts a
    # fix build by itself, and how many fix builds in a row have run since checks last passed.
    checks_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pages.id", ondelete="SET NULL")
    )
    ci_auto_fix: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    ci_fix_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
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
    # Stage F5: the source's text when the factory last took it in (the brief was written,
    # or its changes became change requests), to tell when the spec has changed since.
    seen_text: Mapped[str | None] = mapped_column(Text)
    seen_hash: Mapped[str | None] = mapped_column(String(64))
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class FactoryRelease(Base):
    """An approved build: a frozen copy of the files, numbered per factory (v1, v2...)."""

    __tablename__ = "factory_releases"
    __table_args__ = (
        UniqueConstraint("factory_id", "number", name="uq_factory_releases_factory_number"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    factory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factory_projects.page_id", ondelete="CASCADE"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factory_runs.id", ondelete="SET NULL")
    )
    stack: Mapped[str] = mapped_column(String(40), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    # {path: content}: the app as built, without the packaging files added on download.
    files: Mapped[dict] = mapped_column(JSONB, nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    # {added: [...], changed: [...], removed: [...]} against the release before it.
    changes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )


class FactoryPush(Base):
    """A push of a release to a git repository. Tokens are used once and never stored."""

    __tablename__ = "factory_pushes"
    __table_args__ = (Index("ix_factory_pushes_release_id", "release_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factory_releases.id", ondelete="CASCADE"), nullable=False
    )
    # The repository URL without credentials.
    remote: Mapped[str] = mapped_column(String(500), nullable=False)
    branch: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # pushed, unchanged, failed
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    pushed_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )


class ChangeStatus(enum.StrEnum):
    OPEN = "open"  # requested; not planned yet
    PLANNED = "planned"  # tasks were added to the Tasks database for it
    BUILT = "built"  # a build finished its tasks with passing tests
    RELEASED = "released"  # part of a release
    DISMISSED = "dismissed"


class ChangeOrigin(enum.StrEnum):
    REVIEW = "review"  # a person asked for it
    SPEC = "spec"  # found in edits to the source pages
    CI = "ci"  # the checks failed; the request carries what failed and its log


class FactoryChange(Base):
    """A change request on a built app. Planning turns it into tasks; the next passed build
    builds only those tasks, and the next release includes it."""

    __tablename__ = "factory_changes"
    __table_args__ = (
        UniqueConstraint("factory_id", "number", name="uq_factory_changes_factory_number"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    factory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factory_projects.page_id", ondelete="CASCADE"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ChangeOrigin.REVIEW.value
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ChangeStatus.OPEN.value)
    # Titles of the tasks planning added for it.
    tasks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factory_releases.id", ondelete="SET NULL")
    )
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )
    planned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CiStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"  # every step passed
    FAILED = "failed"  # a step failed: the app has a problem
    ERROR = "error"  # the checks could not run (sandbox down, files missing)


class CiTrigger(enum.StrEnum):
    BUILD = "build"  # after a passed build
    MANUAL = "manual"  # someone pressed Run checks


class FactoryCiRun(Base):
    """One run of an app's checks over a snapshot of its files.

    ``steps`` holds each step's outcome: ``{key, name, command, status, exit_code,
    seconds, output}`` with status ``passed``, ``failed`` or ``skipped`` (after a failure).
    ``files_hash`` identifies the files checked, so a release can tell whether the files it
    would ship are the ones that passed.
    """

    __tablename__ = "factory_ci_runs"
    __table_args__ = (
        UniqueConstraint("factory_id", "number", name="uq_factory_ci_runs_factory_number"),
        Index("ix_factory_ci_runs_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    factory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("factory_projects.page_id", ondelete="CASCADE"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=CiStatus.QUEUED.value)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    # The build it checked, when it ran after one.
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factory_runs.id", ondelete="SET NULL")
    )
    stack: Mapped[str] = mapped_column(String(40), nullable=False)
    sandbox: Mapped[str | None] = mapped_column(String(20))
    files_hash: Mapped[str | None] = mapped_column(String(64))
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # True when the demo sandbox only checked the files and nothing really ran.
    simulated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    summary: Mapped[str | None] = mapped_column(Text)
    # The fix request a failure opened (or updated).
    change_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factory_changes.id", ondelete="SET NULL")
    )
    # The build a failure started by itself (auto-fix).
    fix_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("factory_runs.id", ondelete="SET NULL")
    )
    started_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
