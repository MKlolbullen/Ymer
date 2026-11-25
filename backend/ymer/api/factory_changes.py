"""Factory stage F5: iterating on a built app.

Routes, under ``/api/workspaces/<ws>/factories/<factory page id>``:

- ``POST  /changes``            {title, detail?}: request a change
- ``PATCH /changes/<id>``       {title?, detail?, status?}: edit, dismiss or reopen one
- ``POST  /changes/from-spec``  turn edits to the source pages into change requests
- ``POST  /changes/plan``       plan the open requests as new tasks in the Tasks database

and ``GET``/``PUT /api/workspaces/<ws>/factory-budget``: the workspace's monthly token
budget for builds (``{monthly_tokens}``; owners only, null for the server default).

Planning moves the factory back to "planned", so the next build works through the new
tasks only (Done tasks are skipped) from the files the last build left. A passed build
marks the planned requests built, and the next release includes them.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Literal

from flask import Blueprint, current_app, jsonify
from pydantic import BaseModel, Field
from sqlalchemy import select
from werkzeug.exceptions import Conflict, NotFound

from ymer.ai import AiError, ChatRequest
from ymer.ai.indexing import mark_dirty
from ymer.ai.personal import user_ai
from ymer.api.ai import ai_endpoint, page_text
from ymer.api.factory import (
    ITERABLE,
    _active_run,
    _factory,
    _visible,
    changed_sources,
    factory_json,
)
from ymer.api.validation import parse_body
from ymer.api.workspaces import scope_for
from ymer.app import settings_for
from ymer.auth import current_user, login_required
from ymer.db import db_session
from ymer.factory.budget import MAX_MONTHLY_TOKENS, budget_json
from ymer.factory.build import read_tasks
from ymer.factory.changes import (
    PLAN_SYSTEM,
    SPEC_SCHEMA,
    SPEC_SYSTEM,
    append_tasks,
    changes_of,
    clean_changes,
    clean_plan,
    next_number,
    plan_messages,
    plan_schema,
    spec_diff,
    spec_messages,
    text_hash,
)
from ymer.models import (
    ChangeOrigin,
    ChangeStatus,
    FactoryChange,
    FactoryFile,
    FactoryProject,
    FactoryStage,
    Role,
)
from ymer.permissions import WorkspaceScope

changes_bp = Blueprint(
    "factory_changes",
    __name__,
    url_prefix="/workspaces/<uuid:workspace_id>/factories/<uuid:page_id>",
)
budget_bp = Blueprint("factory_budget", __name__, url_prefix="/workspaces/<uuid:workspace_id>")

MAX_OPEN_CHANGES = 20


def _require_iterable(project: FactoryProject) -> None:
    if project.stage not in ITERABLE:
        raise Conflict("Approve the architecture first. Until then, change the brief instead.")


def _change(project: FactoryProject, change_id: uuid.UUID) -> FactoryChange:
    change = db_session.get(FactoryChange, change_id)
    if change is None or change.factory_id != project.page_id:
        raise NotFound("No such change request.")
    return change


def _open_count(project: FactoryProject) -> int:
    return sum(c.status == ChangeStatus.OPEN.value for c in changes_of(db_session, project.page_id))


def _add_change(project: FactoryProject, title: str, detail: str, origin: ChangeOrigin) -> None:
    db_session.add(
        FactoryChange(
            factory_id=project.page_id,
            number=next_number(db_session, project.page_id),
            title=title,
            detail=detail,
            origin=origin.value,
            status=ChangeStatus.OPEN.value,
            tasks=[],
            created_by_id=current_user().id,
        )
    )
    db_session.flush()


def _text_of(scope: WorkspaceScope, page_id: uuid.UUID | None) -> str:
    seen = _visible(scope, page_id) if page_id else None
    return page_text(seen[0]).strip() if seen else ""


class ChangeBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=4000)


@changes_bp.post("/changes")
@login_required
def add_change(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    _require_iterable(project)
    body = parse_body(ChangeBody)
    if _open_count(project) >= MAX_OPEN_CHANGES:
        raise Conflict(f"There are already {MAX_OPEN_CHANGES} open requests. Plan them first.")
    title = " ".join(body.title.split())
    if not title:
        raise Conflict("Give the change a title.")
    _add_change(project, title, body.detail.strip(), ChangeOrigin.REVIEW)
    data = factory_json(scope, page, project)
    db_session.commit()
    return jsonify(factory=data), 201


class ChangeUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=4000)
    status: Literal["open", "dismissed"] | None = None


@changes_bp.patch("/changes/<uuid:change_id>")
@login_required
def update_change(workspace_id: uuid.UUID, page_id: uuid.UUID, change_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    change = _change(project, change_id)
    if change.status not in (ChangeStatus.OPEN.value, ChangeStatus.DISMISSED.value):
        raise Conflict("This change is already planned. Edit its tasks in the Tasks database.")
    body = parse_body(ChangeUpdate)
    if body.title is not None and " ".join(body.title.split()):
        change.title = " ".join(body.title.split())
    if body.detail is not None:
        change.detail = body.detail.strip()
    if body.status is not None:
        change.status = body.status
    db_session.flush()
    data = factory_json(scope, page, project)
    db_session.commit()
    return jsonify(factory=data)


@changes_bp.post("/changes/from-spec")
@ai_endpoint
def changes_from_spec(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    _require_iterable(project)
    changed = changed_sources(scope, project)
    if not changed:
        raise Conflict("The source pages have not changed since the factory last read them.")
    chat = user_ai().require_chat()
    diffs = [
        (p.title or "Untitled", spec_diff(source.seen_text or "", text))
        for source, p, text in changed
    ]
    raw = chat.complete(
        ChatRequest(
            task="factory.spec_changes",
            system=SPEC_SYSTEM,
            messages=spec_messages(page.title, _text_of(scope, project.brief_page_id), diffs),
            payload={"title": page.title, "diffs": [{"title": t, "diff": d} for t, d in diffs]},
            schema=SPEC_SCHEMA,
            max_tokens=8000,
        )
    )
    try:
        found = clean_changes(json.loads(raw))
    except ValueError as exc:
        raise AiError("The model's answer was not in the expected format. Try again.") from exc
    room = max(0, MAX_OPEN_CHANGES - _open_count(project))
    for item in found[:room]:
        _add_change(project, item["title"], item["detail"], ChangeOrigin.SPEC)
    # The edits are taken in, so the sources count as unchanged until they are edited again.
    now = datetime.now(UTC)
    for source, _p, text in changed:
        source.seen_text, source.seen_hash, source.seen_at = text, text_hash(text), now
    db_session.flush()
    data = factory_json(scope, page, project)
    db_session.commit()
    return jsonify(factory=data, created=min(len(found), room), demo=user_ai().demo), 201


@changes_bp.post("/changes/plan")
@ai_endpoint
def plan_changes(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    _require_iterable(project)
    if _active_run(project) is not None:
        raise Conflict("A build is running. Plan the changes when it has finished.")
    pending = [
        c for c in changes_of(db_session, project.page_id) if c.status == ChangeStatus.OPEN.value
    ]
    if not pending:
        raise Conflict("There are no open change requests to plan.")
    tasks_page = _visible(scope, project.tasks_page_id) if project.tasks_page_id else None
    if tasks_page is None or not tasks_page[1].at_least(Role.EDITOR):
        raise Conflict("You need to be able to edit the Tasks database to plan changes.")
    chat = user_ai().require_chat()

    tasks = read_tasks(db_session, project.tasks_page_id)
    paths = list(
        db_session.scalars(
            select(FactoryFile.path)
            .where(FactoryFile.factory_id == project.page_id)
            .order_by(FactoryFile.path)
        )
    )
    changes = [{"number": c.number, "title": c.title, "detail": c.detail} for c in pending]
    components = list(dict.fromkeys(t["component"] for t in tasks if t["component"]))
    raw = chat.complete(
        ChatRequest(
            task="factory.change_plan",
            system=PLAN_SYSTEM,
            messages=plan_messages(
                page.title,
                project.stack,
                _text_of(scope, project.brief_page_id),
                _text_of(scope, project.architecture_page_id),
                tasks,
                paths,
                changes,
            ),
            payload={"title": page.title, "changes": changes, "components": components},
            schema=plan_schema(),
            max_tokens=16000,
        )
    )
    try:
        planned = clean_plan(json.loads(raw), changes, [t["title"] for t in tasks])
    except ValueError as exc:
        raise AiError("The model's answer was not in the expected format. Try again.") from exc

    append_tasks(db_session, tasks_page[0], planned, user_id=current_user().id)
    mark_dirty(db_session, [tasks_page[0].id])
    now = datetime.now(UTC)
    for change in pending:
        change.status = ChangeStatus.PLANNED.value
        change.tasks = [t["title"] for t in planned if t["change"] == change.number]
        change.planned_at = now
    # Back to "planned": the next build works through the new tasks.
    project.stage = FactoryStage.PLANNED.value
    db_session.flush()
    data = factory_json(scope, page, project)
    db_session.commit()
    return jsonify(factory=data, planned=len(planned), demo=user_ai().demo), 201


# --- the workspace's monthly budget ----------------------------------------------------


class BudgetBody(BaseModel):
    # None: use the server's default. 0: no limit.
    monthly_tokens: int | None = Field(default=None, ge=0, le=MAX_MONTHLY_TOKENS)


@budget_bp.get("/factory-budget")
@login_required
def get_budget(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    scope.require_workspace_role(Role.VIEWER)
    return jsonify(
        budget=budget_json(
            db_session, scope.workspace, settings_for(current_app), can_edit=scope.is_owner
        )
    )


@budget_bp.put("/factory-budget")
@login_required
def set_budget(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    scope.require_workspace_role(Role.OWNER)
    body = parse_body(BudgetBody)
    scope.workspace.factory_monthly_tokens = body.monthly_tokens
    db_session.flush()
    data = budget_json(db_session, scope.workspace, settings_for(current_app), can_edit=True)
    db_session.commit()
    return jsonify(budget=data)
