"""Pages and page permissions, always scoped to one workspace.

- ``GET    /api/workspaces/<ws>/pages[?parent_id=<id>|?roots=1]``  pages I can see
- ``POST   /api/workspaces/<ws>/pages``           editor on the workspace (root) or on parent
- ``GET    /api/workspaces/<ws>/pages/<id>``      viewer
- ``PATCH  /api/workspaces/<ws>/pages/<id>``      editor (moving also needs editor on target);
  ``is_template`` saves the page (and its sub-pages) as a template for the workspace
- ``DELETE /api/workspaces/<ws>/pages/<id>``      editor; deletes the subtree
- ``POST   /api/workspaces/<ws>/pages/<id>/move``  editor; {parent_id, before_id?} places the
  page among its new siblings (at the end without ``before_id``) and renumbers them
- ``GET    /api/workspaces/<ws>/pages/<id>/backlinks``   viewer; visible pages mentioning it
- ``GET    /api/workspaces/<ws>/pages/<id>/permissions``             editor
- ``POST   /api/workspaces/<ws>/pages/<id>/permissions``             editor; {email, role}
- ``PUT    /api/workspaces/<ws>/pages/<id>/permissions/<user_id>``   editor; grant ≤ own role
- ``DELETE /api/workspaces/<ws>/pages/<id>/permissions/<user_id>``   editor

See :mod:`ymer.permissions` for how the effective role is worked out.
"""

import uuid

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from werkzeug.exceptions import BadRequest, Forbidden, NotFound

from ymer import audit
from ymer.ai.indexing import mark_dirty
from ymer.api.validation import parse_body
from ymer.api.workspaces import scope_for
from ymer.auth import current_user, login_required
from ymer.databases import create_default_schema
from ymer.db import db_session
from ymer.files import delete_stored_objects
from ymer.models import (
    FactoryProject,
    File,
    Page,
    PageLink,
    PagePermission,
    PageType,
    Role,
    User,
)
from ymer.permissions import WorkspaceScope

pages_bp = Blueprint("pages", __name__, url_prefix="/workspaces/<uuid:workspace_id>/pages")


class CreatePageBody(BaseModel):
    type: PageType
    title: str = Field(default="", max_length=500)
    parent_id: uuid.UUID | None = None
    position: int = 0


class UpdatePageBody(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    is_template: bool | None = None
    parent_id: uuid.UUID | None = None
    position: int | None = None


class GrantBody(BaseModel):
    role: Role


class GrantByEmailBody(BaseModel):
    email: EmailStr
    role: Role


class MoveBody(BaseModel):
    parent_id: uuid.UUID | None = None
    before_id: uuid.UUID | None = None


def page_json(page: Page, role: Role) -> dict:
    return {
        "id": str(page.id),
        "workspace_id": str(page.workspace_id),
        "parent_id": str(page.parent_id) if page.parent_id else None,
        "type": page.type.value,
        "title": page.title,
        "position": page.position,
        "is_template": page.is_template,
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
    if page.type is PageType.DATABASE:
        db_session.flush()
        create_default_schema(db_session, page)
    db_session.flush()
    if page.type is PageType.FACTORY:
        db_session.add(FactoryProject(page_id=page.id))
    mark_dirty(db_session, [page.id])
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
    if body.title is not None and body.title != page.title:
        page.title = body.title
        mark_dirty(db_session, [page.id])
    if body.position is not None:
        page.position = body.position
    if body.is_template is not None and body.is_template != page.is_template:
        page.is_template = body.is_template
        audit.record(
            "page.template" if body.is_template else "page.untemplate",
            actor=current_user(),
            workspace_id=workspace_id,
            target=("page", page.id),
            details={"title": page.title},
        )
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
    audit.record(
        "page.delete",
        actor=current_user(),
        workspace_id=workspace_id,
        target=("page", page.id),
        details={"title": page.title, "pages": len(subtree), "files": len(keys)},
    )
    db_session.delete(page)
    db_session.commit()
    delete_stored_objects(keys)
    return "", 204


@pages_bp.post("/<uuid:page_id>/move")
@login_required
def move_page(workspace_id: uuid.UUID, page_id: uuid.UUID):
    """Drag and drop in the page tree: a new parent and a place among its children.

    Siblings are renumbered 0..n, including ones the mover cannot see; positions only
    order the tree, so that changes nothing anyone can read.
    """
    scope = scope_for(workspace_id)
    page = scope.get_page(page_id, Role.EDITOR)
    body = parse_body(MoveBody)
    new_parent = body.parent_id
    if new_parent != page.parent_id:
        if new_parent is not None and scope.is_descendant(new_parent, page.id):
            raise BadRequest("A page cannot be moved under itself or its own children.")
        _require_can_create_under(scope, new_parent)
        page.parent_id = new_parent
    same_parent = Page.parent_id.is_(None) if new_parent is None else Page.parent_id == new_parent
    siblings = list(
        db_session.scalars(
            scope.pages_query()
            .where(same_parent, Page.id != page.id)
            .order_by(Page.position, Page.created_at)
            .with_for_update()
        )
    )
    index = len(siblings)
    if body.before_id is not None:
        index = next((i for i, p in enumerate(siblings) if p.id == body.before_id), -1)
        if index < 0:
            raise BadRequest("before_id must be a page under the new parent.")
    siblings.insert(index, page)
    for position, sibling in enumerate(siblings):
        if sibling.position != position:
            sibling.position = position
    db_session.commit()
    scope.forget_page_roles()  # moving can change inherited grants
    return jsonify(page=page_json(page, scope.page_role(page)))


@pages_bp.get("/<uuid:page_id>/backlinks")
@login_required
def list_backlinks(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = scope.get_page(page_id)
    sources = db_session.scalars(
        scope.pages_query()
        .join(PageLink, PageLink.source_page_id == Page.id)
        .where(PageLink.target_page_id == page.id)
        .order_by(Page.title)
    ).all()
    visible = [(p, scope.page_role(p)) for p in sources]
    return jsonify(pages=[page_json(p, r) for p, r in visible if r is not None])


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


def _grant(scope: WorkspaceScope, page: Page, target: User | None, role: Role) -> dict:
    if role is Role.OWNER:
        raise BadRequest("Pages cannot be granted the owner role.")
    if not scope.page_role(page).at_least(role):
        raise Forbidden("You cannot grant a role higher than your own.")
    if target is None or not target.is_active:
        raise NotFound("User not found.")
    grant = db_session.scalar(
        select(PagePermission).where(
            PagePermission.page_id == page.id, PagePermission.user_id == target.id
        )
    )
    if grant is None:
        grant = PagePermission(page_id=page.id, user_id=target.id, granted_by_id=current_user().id)
        db_session.add(grant)
    if grant.role is not role:
        audit.record(
            "page.share",
            actor=current_user(),
            workspace_id=page.workspace_id,
            target=("page", page.id),
            details={"user_id": str(target.id), "email": target.email, "role": role.value},
        )
    grant.role = role
    db_session.commit()
    return {
        "user_id": str(target.id),
        "email": target.email,
        "display_name": target.display_name,
        "role": grant.role.value,
    }


@pages_bp.post("/<uuid:page_id>/permissions")
@login_required
def share_by_email(workspace_id: uuid.UUID, page_id: uuid.UUID):
    """Share with someone by email; they need an account (no invitations yet)."""
    scope = scope_for(workspace_id)
    page = scope.get_page(page_id, Role.EDITOR)
    body = parse_body(GrantByEmailBody)
    target = db_session.scalar(select(User).where(User.email == body.email.strip().lower()))
    if target is None or not target.is_active:
        raise NotFound("No account with this email.")
    return jsonify(permission=_grant(scope, page, target, body.role))


@pages_bp.put("/<uuid:page_id>/permissions/<uuid:user_id>")
@login_required
def set_grant(workspace_id: uuid.UUID, page_id: uuid.UUID, user_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = scope.get_page(page_id, Role.EDITOR)
    body = parse_body(GrantBody)
    return jsonify(permission=_grant(scope, page, db_session.get(User, user_id), body.role))


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
    audit.record(
        "page.unshare",
        actor=current_user(),
        workspace_id=workspace_id,
        target=("page", page.id),
        details={"user_id": str(user_id), "role": grant.role.value},
    )
    db_session.delete(grant)
    db_session.commit()
    return "", 204
