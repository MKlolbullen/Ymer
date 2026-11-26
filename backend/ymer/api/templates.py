"""Templates, imports and duplicating pages.

- ``GET  /api/templates``                               built-in templates (for new workspaces)
- ``GET  /api/workspaces/<ws>/templates``               built-in ones plus pages saved as templates
- ``POST /api/workspaces/<ws>/templates/apply``         {template_id | page_id, parent_id?}
  editor where the pages go; a saved template also needs viewer on the template page
- ``POST /api/workspaces/<ws>/import``                  multipart ``file`` (one or more: .zip,
  .md, .csv) and ``parent_id``; editor where the pages go
- ``POST /api/workspaces/<ws>/pages/<id>/duplicate``    {parent_id?}; viewer on the page,
  editor where the copy goes (next to the original by default)

Saving a page as a template is ``PATCH .../pages/<id>`` with ``{"is_template": true}``.
See :mod:`ymer.importing`, :mod:`ymer.templates` and :mod:`ymer.duplicate`.
"""

import uuid

from flask import Blueprint, current_app, jsonify, request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from werkzeug.exceptions import BadRequest, NotFound, RequestEntityTooLarge, UnprocessableEntity

from ymer import audit, ratelimit
from ymer.api.pages import _require_can_create_under, page_json
from ymer.api.validation import parse_body
from ymer.api.workspaces import scope_for
from ymer.app import settings_for
from ymer.auth import current_user, login_required
from ymer.db import db_session
from ymer.duplicate import TooLarge, copy_page
from ymer.extensions import get_s3
from ymer.importing.bundle import ImportProblem, build_plan, read_uploads
from ymer.importing.run import Result, Target, run_plan
from ymer.models import Page, Role
from ymer.permissions import WorkspaceScope
from ymer.templates import BY_ID, PAGE_TEMPLATES, WORKSPACE_TEMPLATES, plan_for

templates_bp = Blueprint("templates", __name__)


class ApplyBody(BaseModel):
    template_id: str | None = Field(default=None, max_length=100)
    page_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def one_source(self):
        if (self.template_id is None) == (self.page_id is None):
            raise ValueError("Give either template_id or page_id.")
        return self


class DuplicateBody(BaseModel):
    parent_id: uuid.UUID | None = None


def _limit() -> None:
    ratelimit.hit(
        "import",
        str(current_user().id),
        settings_for(current_app).rate_limit_imports,
        60,
        "Too many imports. Wait a minute and try again.",
    )


def target_for(
    workspace_id: uuid.UUID, parent_id: uuid.UUID | None, *, user_id: uuid.UUID, files: bool = True
) -> Target:
    settings = settings_for(current_app)
    return Target(
        workspace_id=workspace_id,
        parent_id=parent_id,
        user_id=user_id,
        version_interval=settings.version_interval,
        max_file_bytes=settings.max_upload_bytes,
        s3=get_s3() if files else None,
        bucket=settings.s3_bucket,
    )


def apply_builtin(
    workspace_id: uuid.UUID, template_id: str, parent_id: uuid.UUID | None, *, user_id: uuid.UUID
) -> Result:
    template = BY_ID.get(template_id)
    if template is None:
        raise NotFound("No such template.")
    return run_plan(
        db_session,
        plan_for(template),
        target_for(workspace_id, parent_id, user_id=user_id, files=False),
    )


def _custom_templates(scope: WorkspaceScope) -> list[Page]:
    pages = db_session.scalars(
        scope.pages_query().where(Page.is_template.is_(True)).order_by(Page.title)
    )
    return [p for p in pages if scope.page_role(p) is not None]


def _next_position(workspace_id: uuid.UUID, parent_id: uuid.UUID | None) -> int:
    parent = Page.parent_id.is_(None) if parent_id is None else Page.parent_id == parent_id
    current = db_session.scalar(
        select(func.max(Page.position)).where(Page.workspace_id == workspace_id, parent)
    )
    return (current or 0) + 1


def _copy(scope: WorkspaceScope, source: Page, parent_id: uuid.UUID | None, title: str):
    settings = settings_for(current_app)
    try:
        return copy_page(
            db_session,
            scope,
            source,
            parent_id=parent_id,
            title=title,
            position=_next_position(source.workspace_id, parent_id),
            user_id=current_user().id,
            version_interval=settings.version_interval,
            s3=get_s3(),
            bucket=settings.s3_bucket,
        )
    except TooLarge as exc:
        raise UnprocessableEntity(str(exc)) from exc


@templates_bp.get("/templates")
@login_required
def list_builtin():
    return jsonify(
        page=[t.json() for t in PAGE_TEMPLATES],
        workspace=[t.json() for t in WORKSPACE_TEMPLATES],
    )


@templates_bp.get("/workspaces/<uuid:workspace_id>/templates")
@login_required
def list_templates(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    return jsonify(
        page=[t.json() for t in PAGE_TEMPLATES],
        workspace=[t.json() for t in WORKSPACE_TEMPLATES],
        custom=[page_json(p, scope.page_role(p)) for p in _custom_templates(scope)],
    )


@templates_bp.post("/workspaces/<uuid:workspace_id>/templates/apply")
@login_required
def apply_template(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    body = parse_body(ApplyBody)
    _require_can_create_under(scope, body.parent_id)
    _limit()
    user = current_user()
    if body.page_id is not None:
        source = scope.get_page(body.page_id)
        if not source.is_template:
            raise BadRequest("That page is not saved as a template.")
        copied = _copy(scope, source, body.parent_id, source.title)
        roots, summary = (
            [copied.root],
            {"pages": copied.pages, "files": copied.files, "warnings": copied.warnings},
        )
        name = source.title
    else:
        result = apply_builtin(
            workspace_id, body.template_id or "", body.parent_id, user_id=user.id
        )
        roots, summary, name = result.roots, result.summary(), BY_ID[body.template_id].name
    audit.record(
        "template.apply",
        actor=user,
        workspace_id=workspace_id,
        target=("page", roots[0].id) if roots else None,
        details={
            "template": body.template_id or str(body.page_id),
            "name": name,
            "pages": summary["pages"],
        },
    )
    db_session.commit()
    pages = [page_json(p, scope.page_role(p)) for p in roots]
    return jsonify(page=pages[0] if pages else None, pages=pages, summary=summary), 201


@templates_bp.post("/workspaces/<uuid:workspace_id>/import")
@login_required
def import_files(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    settings = settings_for(current_app)
    request.max_content_length = settings.max_import_bytes + 1024 * 1024
    raw_parent = request.form.get("parent_id") or None
    try:
        parent_id = uuid.UUID(raw_parent) if raw_parent else None
    except ValueError as exc:
        raise BadRequest("parent_id must be a UUID.") from exc
    _require_can_create_under(scope, parent_id)
    uploads = request.files.getlist("file")
    if not uploads:
        raise BadRequest("Send the files in multipart fields named 'file'.")
    _limit()
    raw: list[tuple[str, bytes]] = []
    total = 0
    for upload in uploads:
        data = upload.read()
        total += len(data)
        if total > settings.max_import_bytes:
            raise RequestEntityTooLarge(
                f"Imports can be at most {settings.max_import_bytes // (1024 * 1024)} MB."
            )
        raw.append((upload.filename or "import", data))
    try:
        plan = build_plan(read_uploads(raw), source_name=raw[0][0] if len(raw) == 1 else None)
    except ImportProblem as exc:
        raise UnprocessableEntity(str(exc)) from exc

    user = current_user()
    result = run_plan(db_session, plan, target_for(workspace_id, parent_id, user_id=user.id))
    audit.record(
        "import",
        actor=user,
        workspace_id=workspace_id,
        target=("page", result.roots[0].id) if result.roots else None,
        details={
            "files": [name for name, _ in raw][:20],
            "pages": result.pages,
            "databases": result.databases,
            "rows": result.rows,
            "attachments": result.files,
        },
    )
    db_session.commit()
    return jsonify(
        pages=[page_json(p, scope.page_role(p)) for p in result.roots],
        summary=result.summary(),
    ), 201


@templates_bp.post("/workspaces/<uuid:workspace_id>/pages/<uuid:page_id>/duplicate")
@login_required
def duplicate_page(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    source = scope.get_page(page_id, Role.VIEWER)
    body = parse_body(DuplicateBody)
    parent_id = body.parent_id if "parent_id" in body.model_fields_set else source.parent_id
    _require_can_create_under(scope, parent_id)
    _limit()
    title = f"{source.title or 'Untitled'} (copy)"[:500]
    copied = _copy(scope, source, parent_id, title)
    audit.record(
        "page.duplicate",
        actor=current_user(),
        workspace_id=workspace_id,
        target=("page", copied.root.id),
        details={"source": str(source.id), "pages": copied.pages},
    )
    db_session.commit()
    return jsonify(
        page=page_json(copied.root, scope.page_role(copied.root)),
        summary={"pages": copied.pages, "files": copied.files, "warnings": copied.warnings},
    ), 201
