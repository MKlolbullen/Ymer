"""The Factory, stages F1 (the brief) and F2 (the architecture).

Routes, under ``/api/workspaces/<ws>/factories/<factory page id>``:

- ``GET    ``                     the factory's state: stage, sources, questions, brief
- ``PUT    /sources``              {page_ids}: the pages it works from, in order
- ``POST   /brief``                write (or rewrite) the brief from the sources
- ``PATCH  /questions/<id>``       {answer?, status?}: answer, dismiss or reopen a question
- ``POST   /approve``              approve the brief (no open questions left)
- ``POST   /architecture``         {stack?}: design the architecture from the approved brief
- ``POST   /approve-plan``         approve the architecture and its tasks
- ``POST   /reopen``               step back one approval (the plan, else the brief)

Reading the factory needs viewer access; everything else needs editor. Sources are read
with the caller's own access, so a source they cannot open is left out of the brief they
write, and shows as hidden in the list.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from flask import Blueprint, current_app, jsonify
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from werkzeug.exceptions import BadRequest, Conflict, NotFound, UnprocessableEntity

from ymer.ai import AiError, ChatRequest, get_ai
from ymer.ai.generate import create_database, mindmap_state
from ymer.ai.indexing import mark_dirty
from ymer.api.ai import ai_endpoint, page_text
from ymer.api.documents import _person
from ymer.api.pages import page_json
from ymer.api.validation import parse_body
from ymer.api.workspaces import scope_for
from ymer.app import settings_for
from ymer.auth import current_user, login_required
from ymer.content import index_document
from ymer.db import db_session
from ymer.documents import save_state
from ymer.factory.architecture import (
    ARCHITECTURE_SYSTEM,
    architecture_blocks,
    architecture_messages,
    architecture_schema,
    clean_architecture,
    system_map_spec,
    tasks_database_spec,
)
from ymer.factory.brief import (
    BRIEF_SCHEMA,
    BRIEF_SYSTEM,
    brief_blocks,
    brief_messages,
    clean_brief,
    fit_sources,
)
from ymer.factory.document import document_state
from ymer.factory.stacks import STACKS, stacks_json
from ymer.models import (
    DatabaseProperty,
    DatabaseRow,
    DatabaseView,
    DocumentContent,
    DocumentVersion,
    FactoryProject,
    FactoryQuestion,
    FactorySource,
    FactoryStage,
    Page,
    PageType,
    QuestionStatus,
    Role,
    User,
    ViewType,
)
from ymer.permissions import WorkspaceScope

factory_bp = Blueprint(
    "factory", __name__, url_prefix="/workspaces/<uuid:workspace_id>/factories/<uuid:page_id>"
)

MAX_SOURCES = 20
BRIEF_TITLE = "Brief"


def ensure_project(page: Page) -> FactoryProject:
    project = db_session.get(FactoryProject, page.id)
    if project is None:
        project = FactoryProject(page_id=page.id, stage=FactoryStage.SOURCES.value)
        db_session.add(project)
        db_session.flush()
    return project


def _factory(
    scope: WorkspaceScope, page_id: uuid.UUID, minimum: Role
) -> tuple[Page, FactoryProject]:
    page = scope.get_page(page_id, minimum)
    if page.type is not PageType.FACTORY:
        raise NotFound()
    return page, ensure_project(page)


def _sources(project: FactoryProject) -> list[FactorySource]:
    return list(
        db_session.scalars(
            select(FactorySource)
            .where(FactorySource.factory_id == project.page_id)
            .order_by(FactorySource.position, FactorySource.created_at)
        )
    )


def _questions(project: FactoryProject) -> list[FactoryQuestion]:
    return list(
        db_session.scalars(
            select(FactoryQuestion)
            .where(FactoryQuestion.factory_id == project.page_id)
            .order_by(FactoryQuestion.position, FactoryQuestion.created_at)
        )
    )


def _visible(scope: WorkspaceScope, page_id: uuid.UUID) -> tuple[Page, Role] | None:
    page = db_session.get(Page, page_id)
    if page is None or page.workspace_id != scope.workspace.id:
        return None
    role = scope.page_role(page)
    return (page, role) if role is not None else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


BRIEF_OPEN = {FactoryStage.SOURCES.value, FactoryStage.BRIEF.value}


def _require_open(project: FactoryProject) -> None:
    if project.stage not in BRIEF_OPEN:
        raise Conflict("The brief is approved. Reopen it to change it.")


def question_json(q: FactoryQuestion) -> dict:
    return {
        "id": str(q.id),
        "question": q.question,
        "reason": q.reason,
        "status": q.status,
        "answer": q.answer,
        "answered_by": _person(
            db_session.get(User, q.answered_by_id) if q.answered_by_id else None
        ),
        "answered_at": _iso(q.answered_at),
    }


def factory_json(scope: WorkspaceScope, page: Page, project: FactoryProject) -> dict:
    sources = []
    for source in _sources(project):
        seen = _visible(scope, source.page_id)
        if seen is None:
            sources.append({"page_id": None, "title": None, "type": None, "visible": False})
        else:
            src, _role = seen
            sources.append(
                {
                    "page_id": str(src.id),
                    "title": src.title,
                    "type": src.type.value,
                    "visible": True,
                }
            )

    def linked(page_id: uuid.UUID | None) -> dict | None:
        seen = _visible(scope, page_id) if page_id else None
        return page_json(*seen) if seen else None

    def person(user_id: uuid.UUID | None) -> dict | None:
        return _person(db_session.get(User, user_id) if user_id else None)

    tasks = linked(project.tasks_page_id)
    if tasks is not None:
        tasks["count"] = db_session.scalar(
            select(func.count()).where(DatabaseRow.database_id == project.tasks_page_id)
        )
    role = scope.page_role(page)
    return {
        "page_id": str(page.id),
        "stage": project.stage,
        "can_edit": role is not None and role.at_least(Role.EDITOR),
        "sources": sources,
        "questions": [question_json(q) for q in _questions(project)],
        "brief": linked(project.brief_page_id),
        "brief_generated_at": _iso(project.brief_generated_at),
        "approved_at": _iso(project.approved_at),
        "approved_by": person(project.approved_by_id),
        "stack": project.stack,
        "stacks": stacks_json(),
        "architecture": linked(project.architecture_page_id),
        "system_map": linked(project.map_page_id),
        "tasks": tasks,
        "architecture_generated_at": _iso(project.architecture_generated_at),
        "plan_approved_at": _iso(project.plan_approved_at),
        "plan_approved_by": person(project.plan_approved_by_id),
    }


@factory_bp.get("")
@login_required
def get_factory(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.VIEWER)
    data = factory_json(scope, page, project)
    db_session.commit()  # ensure_project may have created the row
    return jsonify(factory=data)


# --- sources --------------------------------------------------------------------------


class SourcesBody(BaseModel):
    page_ids: list[uuid.UUID] = Field(max_length=MAX_SOURCES)


@factory_bp.put("/sources")
@login_required
def set_sources(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    _require_open(project)
    body = parse_body(SourcesBody)
    existing = {s.page_id: s for s in _sources(project)}
    wanted = list(dict.fromkeys(body.page_ids))
    # Sources the caller cannot open stay where they are: they cannot see them to keep them.
    wanted += [pid for pid in existing if pid not in wanted and _visible(scope, pid) is None]
    for source_id in wanted:
        if source_id in (page.id, project.brief_page_id):
            raise BadRequest("A factory cannot use itself or its own brief as a source.")
        if source_id not in existing:
            # Adding a source needs access to it; keeping one someone else added does not.
            source = scope.get_page(source_id, Role.VIEWER)
            if source.type in (PageType.FOLDER, PageType.FACTORY):
                raise BadRequest("Folders and factories cannot be sources.")
    db_session.execute(
        delete(FactorySource).where(
            FactorySource.factory_id == project.page_id, FactorySource.page_id.not_in(wanted)
        )
    )
    user = current_user()
    for position, source_id in enumerate(wanted):
        if source_id in existing:
            existing[source_id].position = position
        else:
            db_session.add(
                FactorySource(
                    factory_id=project.page_id,
                    page_id=source_id,
                    position=position,
                    added_by_id=user.id,
                )
            )
    db_session.flush()
    data = factory_json(scope, page, project)
    db_session.commit()
    return jsonify(factory=data)


# --- brief ----------------------------------------------------------------------------


def _source_texts(scope: WorkspaceScope, project: FactoryProject) -> list[tuple[Page, str]]:
    out = []
    for source in _sources(project):
        seen = _visible(scope, source.page_id)
        if seen is None:
            continue
        text = page_text(seen[0]).strip()
        if text:
            out.append((seen[0], text))
    return out


def _new_child(
    page: Page, previous_id: uuid.UUID | None, title: str, type: PageType, position: int
) -> Page:
    """A new page under the factory. The page it replaces stays, renamed, so nothing a
    person wrote in it is lost (it may be open in someone's editor right now)."""
    old = db_session.get(Page, previous_id) if previous_id else None
    if old is not None and not old.title.endswith("(earlier draft)"):
        old.title = f"{old.title} (earlier draft)"[:500]
        mark_dirty(db_session, [old.id])
    child = Page(
        workspace_id=page.workspace_id,
        parent_id=page.id,
        type=type,
        title=title[:500],
        position=position,
        created_by_id=current_user().id,
    )
    db_session.add(child)
    db_session.flush()
    return child


def _store(child: Page, state: bytes) -> None:
    save_state(
        db_session,
        child.id,
        state,
        user_id=current_user().id,
        version_interval=settings_for(current_app).version_interval,
    )
    index_document(db_session, child, state)
    mark_dirty(db_session, [child.id])


def _write_brief_page(page: Page, project: FactoryProject, title: str, state: bytes) -> Page:
    brief = _new_child(page, project.brief_page_id, title, PageType.DOCUMENT, 0)
    _store(brief, state)
    return brief


@factory_bp.post("/brief")
@ai_endpoint
def write_brief(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    _require_open(project)
    chat = get_ai().require_chat()

    sources = _source_texts(scope, project)
    if not sources:
        raise UnprocessableEntity(
            "Add at least one page with some notes in it, then write the brief."
        )
    fitted = fit_sources([(p.title, text) for p, text in sources])
    questions = _questions(project)
    decisions = [
        (q.question, q.answer or "") for q in questions if q.status == QuestionStatus.ANSWERED.value
    ]
    dismissed = [q.question for q in questions if q.status == QuestionStatus.DISMISSED.value]

    raw = chat.complete(
        ChatRequest(
            task="factory.brief",
            system=BRIEF_SYSTEM,
            messages=brief_messages(page.title, fitted, decisions, dismissed),
            payload={
                "title": page.title,
                "sources": [{"title": t, "text": x} for t, x in fitted],
                "decisions": [{"question": q, "answer": a} for q, a in decisions],
                "dismissed": dismissed,
            },
            schema=BRIEF_SCHEMA,
            max_tokens=16000,
        )
    )
    try:
        brief = clean_brief(json.loads(raw))
    except ValueError as exc:
        raise AiError("The model's answer was not in the expected format. Try again.") from exc

    state = document_state(brief_blocks(brief, decisions, [(p.id, p.title) for p, _ in sources]))
    brief_page = _write_brief_page(page, project, BRIEF_TITLE, state)

    # Open questions are replaced by the new ones; answered and dismissed ones stay.
    closed = {q.question.casefold() for q in questions if q.status != QuestionStatus.OPEN.value}
    db_session.execute(
        delete(FactoryQuestion).where(
            FactoryQuestion.factory_id == project.page_id,
            FactoryQuestion.status == QuestionStatus.OPEN.value,
        )
    )
    start = len(questions)
    for i, q in enumerate(brief["questions"]):
        if q["question"].casefold() in closed:
            continue
        db_session.add(
            FactoryQuestion(
                factory_id=project.page_id,
                position=start + i,
                question=q["question"],
                reason=q["why"],
            )
        )
    project.brief_page_id = brief_page.id
    project.brief_generated_at = datetime.now(UTC)
    project.stage = FactoryStage.BRIEF.value
    db_session.flush()
    data = factory_json(scope, page, project)
    db_session.commit()
    return jsonify(factory=data, demo=get_ai().demo), 201


# --- questions ------------------------------------------------------------------------


class QuestionBody(BaseModel):
    answer: str | None = Field(default=None, max_length=4000)
    status: Literal["open", "dismissed"] | None = None


@factory_bp.patch("/questions/<uuid:question_id>")
@login_required
def update_question(workspace_id: uuid.UUID, page_id: uuid.UUID, question_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    _require_open(project)
    question = db_session.get(FactoryQuestion, question_id)
    if question is None or question.factory_id != project.page_id:
        raise NotFound()
    body = parse_body(QuestionBody)
    answer = (body.answer or "").strip()
    if body.answer is not None and answer:
        question.answer = answer
        question.status = QuestionStatus.ANSWERED.value
        question.answered_by_id = current_user().id
        question.answered_at = datetime.now(UTC)
    elif body.status is not None or body.answer is not None:
        # A blank answer reopens the question, like an explicit "open".
        question.status = body.status or QuestionStatus.OPEN.value
        question.answer = None
        question.answered_by_id = None
        question.answered_at = None
    db_session.flush()
    data = factory_json(scope, page, project)
    db_session.commit()
    return jsonify(factory=data)


# --- approval -------------------------------------------------------------------------


@factory_bp.post("/approve")
@login_required
def approve(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    _require_open(project)
    if project.brief_page_id is None or db_session.get(Page, project.brief_page_id) is None:
        raise Conflict("Write the brief first.")
    open_count = sum(q.status == QuestionStatus.OPEN.value for q in _questions(project))
    if open_count:
        raise Conflict(
            f"{open_count} question{'s are' if open_count != 1 else ' is'} still open. "
            "Answer or dismiss them first, then rewrite the brief if the answers change it."
        )
    user = current_user()
    content = db_session.get(DocumentContent, project.brief_page_id)
    if content is not None:
        # A named version marks exactly what was approved in the brief's history.
        db_session.add(
            DocumentVersion(
                page_id=project.brief_page_id,
                state=content.state,
                label="Approved brief",
                created_by_id=user.id,
            )
        )
    project.stage = FactoryStage.APPROVED.value
    project.approved_at = datetime.now(UTC)
    project.approved_by_id = user.id
    db_session.flush()
    data = factory_json(scope, page, project)
    db_session.commit()
    return jsonify(factory=data)


@factory_bp.post("/reopen")
@login_required
def reopen(workspace_id: uuid.UUID, page_id: uuid.UUID):
    """Step back one approval: an approved plan goes back to review, otherwise an approved
    brief opens for changes again (an architecture made from it stays, marked as earlier)."""
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    if project.stage == FactoryStage.PLANNED.value:
        project.stage = FactoryStage.ARCHITECTURE.value
        project.plan_approved_at = None
        project.plan_approved_by_id = None
    elif project.stage in (FactoryStage.APPROVED.value, FactoryStage.ARCHITECTURE.value):
        project.stage = FactoryStage.BRIEF.value
        project.approved_at = None
        project.approved_by_id = None
    db_session.flush()
    data = factory_json(scope, page, project)
    db_session.commit()
    return jsonify(factory=data)


# --- architecture (stage F2) ----------------------------------------------------------


class ArchitectureBody(BaseModel):
    # A stack key, or None to let the model pick one for the brief.
    stack: str | None = None


def _approved_versions(page_id: uuid.UUID | None, label: str, user_id: uuid.UUID) -> None:
    content = db_session.get(DocumentContent, page_id) if page_id else None
    if content is not None:
        db_session.add(
            DocumentVersion(
                page_id=page_id, state=content.state, label=label, created_by_id=user_id
            )
        )


def _hide_on_board(database: Page, names: tuple[str, ...]) -> None:
    """Keep board cards short: long text properties stay in the table and the row panel."""
    db_session.flush()
    hidden = [
        str(p.id)
        for p in db_session.scalars(
            select(DatabaseProperty).where(DatabaseProperty.database_id == database.id)
        )
        if p.name in names
    ]
    for view in db_session.scalars(
        select(DatabaseView).where(
            DatabaseView.database_id == database.id, DatabaseView.type == ViewType.BOARD
        )
    ):
        view.config = {**view.config, "hidden": hidden}


@factory_bp.post("/architecture")
@ai_endpoint
def design_architecture(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    if project.stage not in (FactoryStage.APPROVED.value, FactoryStage.ARCHITECTURE.value):
        raise Conflict(
            "Approve the brief first."
            if project.stage in BRIEF_OPEN
            else "The plan is approved. Reopen it to redesign the architecture."
        )
    body = parse_body(ArchitectureBody)
    if body.stack is not None and body.stack not in STACKS:
        raise BadRequest(f"Unknown stack. Choose one of: {', '.join(STACKS)}.")
    chat = get_ai().require_chat()

    # The brief as it reads now: people may have edited it after it was written.
    brief_page = _visible(scope, project.brief_page_id) if project.brief_page_id else None
    if brief_page is None:
        raise Conflict("The brief is missing. Reopen the factory and write it again.")
    brief_text = page_text(brief_page[0]).strip()
    if not brief_text:
        raise UnprocessableEntity("The brief is empty. Write it again before the architecture.")
    brief_text = fit_sources([("Brief", brief_text)])[0][1]

    raw = chat.complete(
        ChatRequest(
            task="factory.architecture",
            system=ARCHITECTURE_SYSTEM,
            messages=architecture_messages(page.title, brief_text, body.stack),
            payload={"title": page.title, "brief": brief_text, "stack": body.stack},
            schema=architecture_schema(),
            max_tokens=32000,
        )
    )
    try:
        arch = clean_architecture(json.loads(raw), body.stack)
    except ValueError as exc:
        raise AiError("The model's answer was not in the expected format. Try again.") from exc
    if not arch["components"] or not arch["tasks"]:
        raise AiError("The model's design had no components or no tasks. Try again.")

    user = current_user()
    doc = _new_child(page, project.architecture_page_id, "Architecture", PageType.DOCUMENT, 1)
    _store(doc, document_state(architecture_blocks(arch, (brief_page[0].id, brief_page[0].title))))

    map_page = _new_child(page, project.map_page_id, "System map", PageType.MINDMAP, 2)
    state, _count = mindmap_state(system_map_spec(page.title, arch))
    _store(map_page, state)

    tasks_page = _new_child(page, project.tasks_page_id, "Tasks", PageType.DATABASE, 3)
    create_database(db_session, tasks_page, tasks_database_spec(arch), user_id=user.id)
    _hide_on_board(tasks_page, ("Depends on", "Detail"))
    mark_dirty(db_session, [tasks_page.id])

    project.stack = arch["stack"]
    project.architecture_page_id = doc.id
    project.map_page_id = map_page.id
    project.tasks_page_id = tasks_page.id
    project.architecture_generated_at = datetime.now(UTC)
    project.stage = FactoryStage.ARCHITECTURE.value
    db_session.flush()
    data = factory_json(scope, page, project)
    db_session.commit()
    return jsonify(factory=data, demo=get_ai().demo), 201


@factory_bp.post("/approve-plan")
@login_required
def approve_plan(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    if project.stage != FactoryStage.ARCHITECTURE.value:
        raise Conflict(
            "The plan is already approved."
            if project.stage == FactoryStage.PLANNED.value
            else "Design the architecture first."
        )
    user = current_user()
    _approved_versions(project.architecture_page_id, "Approved architecture", user.id)
    _approved_versions(project.map_page_id, "Approved system map", user.id)
    project.stage = FactoryStage.PLANNED.value
    project.plan_approved_at = datetime.now(UTC)
    project.plan_approved_by_id = user.id
    db_session.flush()
    data = factory_json(scope, page, project)
    db_session.commit()
    return jsonify(factory=data)
