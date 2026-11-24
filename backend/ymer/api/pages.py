"""Pages and page permissions, always scoped to one workspace.

- ``GET    /api/workspaces/<ws>/pages[?parent_id=<id>|?roots=1]``  pages I can see
- ``POST   /api/workspaces/<ws>/pages``           editor on the workspace (root) or on parent
- ``GET    /api/workspaces/<ws>/pages/<id>``      viewer
- ``PATCH  /api/workspaces/<ws>/pages/<id>``      editor (moving also needs editor on target)
- ``DELETE /api/workspaces/<ws>/pages/<id>``      editor; deletes the subtree
- ``GET    /api/workspaces/<ws>/pages/<id>/permissions``             editor
- ``PUT    /api/workspaces/<ws>/pages/<id>/permissions/<user_id>``   editor; grant ≤ own role
- ``DELETE /api/workspaces/<ws>/pages/<id>/permissions/<user_id>``   editor

See :mod:`ymer.permissions` for how the effective role is worked out.
"""

import uuid

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, Field
from sqlalchemy import select
from werkzeug.exceptions import BadRequest, Forbidden, NotFound

from ymer.api.validation import parse_body
from ymer.api.workspaces import scope_for
from ymer.auth import current_user, login_required
from ymer.db import db_session
from ymer.files import delete_stored_objects
from ymer.models import File, Page, PagePermission, PageType, Role, User
from ymer.permissions import WorkspaceScope

pages_bp = Blueprint("pages", __name__, url_prefix="/workspaces/<uuid:workspace_id>/pages")


class CreatePageBody(BaseModel):
    type: PageType
    title: str = Field(default="", max_length=500)
    parent_id: uuid.UUID | None = None
    position: int = 0


class UpdatePageBody(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    parent_id: uuid.UUID | None = None
    position: int | None = None


class GrantBody(BaseModel):
    role: Role


def page_json(page: Page, role: Role) -> dict:
    return {
        "id": str(page.id),
        "workspace_id": str(page.workspace_id),
        "parent_id": str(page.parent_id) if page.parent_id else None,
        "type": page.type.value,
        "title": page.title,
        "position": page.position,
        "my_role": role.value,
        "created_at": page.created_at.isoformat() if page.created_at else None,
        "updated_at": page.updated_at.isoformat() if page.updated_at else None,
    }


def _require_can_create_under(scope: WorkspaceScope, parent_id: uuid.UUID | None) -> None:
    if parent_id is None:
        scope.require_workspace_role(Role.EDITOR)
    else:
        scope.get_page(parent_id, Role.EDITOR)


@pages_bp.get("")
@login_required
def list_pages(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    parent = request.args.get("parent_id")
    try:
        parent_id = uuid.UUID(parent) if parent else None
    except ValueError as exc:
        raise BadRequest("parent_id must be a UUID.") from exc
    roots_only = request.args.get("roots") in {"1", "true"}
    rows = scope.visible_pages(parent_id, roots_only=roots_only)
    return jsonify(pages=[page_json(p, r) for p, r in rows])


@pages_bp.post("")
@login_required
def create_page(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    body = parse_body(CreatePageBody)
    _require_can_create_under(scope, body.parent_id)
    page = Page(
        workspace_id=workspace_id,
        parent_id=body.parent_id,
        type=body.type,
        title=body.title,
        position=body.position,
        created_by_id=current_user().id,
    )
    db_session.add(page)
    db_session.commit()
    return jsonify(page=page_json(page, scope.page_role(page))), 201


@pages_bp.get("/<uuid:page_id>")
@login_required
def get_page(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = scope.get_page(page_id)
    return jsonify(page=page_json(page, scope.page_role(page)))


@pages_bp.patch("/<uuid:page_id>")
@login_required
def update_page(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = scope.get_page(page_id, Role.EDITOR)
    body = parse_body(UpdatePageBody)
    if body.title is not None:
        page.title = body.title
    if body.position is not None:
        page.position = body.position
    if "parent_id" in body.model_fields_set and body.parent_id != page.parent_id:
        new_parent = body.parent_id
        if new_parent is not None and scope.is_descendant(new_parent, page.id):
            raise BadRequest("A page cannot be moved under itself or its own children.")
        _require_can_create_under(scope, new_parent)
        page.parent_id = new_parent
    db_session.commit()
    scope.forget_page_roles()  # moving can change inherited grants
    return jsonify(page=page_json(page, scope.page_role(page)))


@pages_bp.delete("/<uuid:page_id>")
@login_required
def delete_page(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = scope.get_page(page_id, Role.EDITOR)
    subtree = scope.subtree_ids(page.id)
    keys = db_session.scalars(select(File.storage_key).where(File.page_id.in_(subtree))).all()
    db_session.delete(page)
    db_session.commit()
    delete_stored_objects(keys)
    return "", 204


@pages_bp.get("/<uuid:page_id>/permissions")
@login_required
def list_grants(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = scope.get_page(page_id, Role.EDITOR)
    rows = db_session.execute(
        select(PagePermission, User)
        .join(User, User.id == PagePermission.user_id)
        .where(PagePermission.page_id == page.id)
        .order_by(PagePermission.created_at)
    ).all()
    return jsonify(
        permissions=[
            {
                "user_id": str(u.id),
                "email": u.email,
                "display_name": u.display_name,
                "role": g.role.value,
            }
            for g, u in rows
        ]
    )


@pages_bp.put("/<uuid:page_id>/permissions/<uuid:user_id>")
@login_required
def set_grant(workspace_id: uuid.UUID, page_id: uuid.UUID, user_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = scope.get_page(page_id, Role.EDITOR)
    body = parse_body(GrantBody)
    if body.role is Role.OWNER:
        raise BadRequest("Pages cannot be granted the owner role.")
    if not scope.page_role(page).at_least(body.role):
        raise Forbidden("You cannot grant a role higher than your own.")
    target = db_session.get(User, user_id)
    if target is None or not target.is_active:
        raise NotFound("User not found.")
    grant = db_session.scalar(
        select(PagePermission).where(
            PagePermission.page_id == page.id, PagePermission.user_id == user_id
        )
    )
    if grant is None:
        grant = PagePermission(page_id=page.id, user_id=user_id, granted_by_id=current_user().id)
        db_session.add(grant)
    grant.role = body.role
    db_session.commit()
    return jsonify(permission={"user_id": str(user_id), "role": grant.role.value})


@pages_bp.delete("/<uuid:page_id>/permissions/<uuid:user_id>")
@login_required
def remove_grant(workspace_id: uuid.UUID, page_id: uuid.UUID, user_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = scope.get_page(page_id, Role.EDITOR)
    grant = db_session.scalar(
        select(PagePermission).where(
            PagePermission.page_id == page.id, PagePermission.user_id == user_id
        )
    )
    if grant is None:
        raise NotFound("Permission not found.")
    db_session.delete(grant)
    db_session.commit()
    return "", 204
