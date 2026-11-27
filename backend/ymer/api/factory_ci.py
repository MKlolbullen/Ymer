"""Factory checks (CI) on built apps.

Routes, under ``/api/workspaces/<ws>/factories/<factory page id>``:

- ``GET   /checks``            recent check runs, the pass rate and the release gate
- ``POST  /checks``            run the checks on the app's current files now
- ``GET   /checks/<n>``        one check run, with each step's output
- ``PUT   /checks/settings``   {auto_fix}: start a fix build by itself when checks fail

Checks also run by themselves after every passed build (``ymer.factory.runner``).
Reading needs viewer access to the factory; running and settings need editor.
"""

import uuid

from flask import Blueprint, current_app, jsonify
from pydantic import BaseModel
from sqlalchemy import select
from werkzeug.exceptions import Conflict, NotFound, ServiceUnavailable

from ymer import audit
from ymer.api.factory import ITERABLE, _active_run, _factory, factory_json
from ymer.api.validation import parse_body
from ymer.api.workspaces import scope_for
from ymer.app import settings_for
from ymer.auth import current_user, login_required
from ymer.db import db_session
from ymer.factory.ci import ACTIVE_CI, checks_json, ci_json, latest_ci, queue_ci
from ymer.factory.releases import current_files
from ymer.factory.runner import sandbox_for, start_checks_inline
from ymer.models import CiTrigger, FactoryCiRun, Role

ci_bp = Blueprint(
    "factory_ci",
    __name__,
    url_prefix="/workspaces/<uuid:workspace_id>/factories/<uuid:page_id>",
)


@ci_bp.get("/checks")
@login_required
def list_checks(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    _page, project = _factory(scope, page_id, Role.VIEWER)
    runs = db_session.scalars(
        select(FactoryCiRun)
        .where(FactoryCiRun.factory_id == project.page_id)
        .order_by(FactoryCiRun.number.desc())
        .limit(50)
    ).all()
    return jsonify(
        checks=checks_json(db_session, project, settings_for(current_app)),
        runs=[ci_json(db_session, r) for r in runs],
    )


@ci_bp.post("/checks")
@login_required
def run_checks(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    if project.stage not in ITERABLE:
        raise Conflict("Approve the architecture and build the app before checking it.")
    if _active_run(project) is not None:
        raise Conflict("A build is running. Its checks run when it has finished.")
    latest = latest_ci(db_session, project.page_id)
    if latest is not None and latest.status in ACTIVE_CI:
        raise Conflict("The checks are already running.")
    if not current_files(db_session, project.page_id):
        raise Conflict("There are no files to check yet. Build the app first.")
    sandbox = sandbox_for(current_app)
    if sandbox is None:
        raise ServiceUnavailable("Checks are turned off on this server.")
    ci = queue_ci(db_session, project, trigger=CiTrigger.MANUAL, user_id=current_user().id)
    audit.record(
        "factory.checks",
        actor=current_user(),
        workspace_id=workspace_id,
        target=("page", page.id),
        details={"checks": ci.number, "sandbox": sandbox.name},
    )
    out = ci_json(db_session, ci)
    db_session.commit()
    if settings_for(current_app).factory_run_inline:
        start_checks_inline(current_app._get_current_object(), ci.id)
    return jsonify(run=out), 202


@ci_bp.get("/checks/<int:number>")
@login_required
def get_checks(workspace_id: uuid.UUID, page_id: uuid.UUID, number: int):
    scope = scope_for(workspace_id)
    _page, project = _factory(scope, page_id, Role.VIEWER)
    ci = db_session.scalars(
        select(FactoryCiRun).where(
            FactoryCiRun.factory_id == project.page_id, FactoryCiRun.number == number
        )
    ).first()
    if ci is None:
        raise NotFound("No such check run.")
    return jsonify(run=ci_json(db_session, ci, output=True))


class CheckSettingsBody(BaseModel):
    auto_fix: bool


@ci_bp.put("/checks/settings")
@login_required
def check_settings(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    body = parse_body(CheckSettingsBody)
    project.ci_auto_fix = body.auto_fix
    if body.auto_fix:
        project.ci_fix_streak = 0  # turning it on again gives it a fresh set of tries
    audit.record(
        "factory.checks_settings",
        actor=current_user(),
        workspace_id=workspace_id,
        target=("page", page.id),
        details={"auto_fix": body.auto_fix},
    )
    db_session.flush()
    data = factory_json(scope, page, project)
    db_session.commit()
    return jsonify(factory=data)
