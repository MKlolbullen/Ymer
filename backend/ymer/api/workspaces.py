"""Workspaces and their members.

- ``GET    /api/workspaces``                         workspaces I can access
- ``POST   /api/workspaces``                         create one; I become its owner
- ``GET    /api/workspaces/<id>``                    any member or guest
- ``PATCH  /api/workspaces/<id>``                    owner
- ``DELETE /api/workspaces/<id>``                    owner
- ``GET    /api/workspaces/<id>/members``            any member
- ``POST   /api/workspaces/<id>/members``            owner; adds an existing user by email
- ``PATCH  /api/workspaces/<id>/members/<user_id>``  owner; change role
- ``GET    /api/workspaces/<id>/audit``              owner; the audit log, newest first
  (``?before=<iso time>&limit=<1..200>&action=<prefix>``)
- ``DELETE /api/workspaces/<id>/members/<user_id>``  owner, or the member leaving; also
  removes that user's page grants in the workspace

A workspace always keeps at least one owner.
"""

import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound

from ymer import audit
from ymer.api.validation import parse_body
from ymer.auth import current_user, login_required
from ymer.db import db_session
from ymer.models import (
    AuditEvent,
    Page,
    PagePermission,
    Role,
    User,
    Workspace,
    WorkspaceMember,
)
from ymer.permissions import WorkspaceScope, workspaces_for_user

workspaces_bp = Blueprint("workspaces", __name__, url_prefix="/workspaces")


class WorkspaceBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class AddMemberBody(BaseModel):
    email: EmailStr
    role: Role = Role.EDITOR


class MemberRoleBody(BaseModel):
    role: Role


def workspace_json(workspace: Workspace, role: Role | None) -> dict:
    return {
        "id": str(workspace.id),
        "name": workspace.name,
        "role": role.value if role else None,
        "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
    }


def member_json(member: WorkspaceMember, user: User) -> dict:
    return {
        "user_id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": member.role.value,
    }


def scope_for(workspace_id: uuid.UUID) -> WorkspaceScope:
    return WorkspaceScope.load(db_session, current_user(), workspace_id)


def _owner_count(workspace_id: uuid.UUID) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.role == Role.OWNER)
    )


def _get_member(workspace_id: uuid.UUID, user_id: uuid.UUID) -> WorkspaceMember:
    member = db_session.scalar(
        select(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id)
        .with_for_update()
    )
    if member is None:
        raise NotFound("Member not found.")
    return member


def _lock_workspace(workspace_id: uuid.UUID) -> None:
    """Serialise owner changes so two requests cannot remove the last two owners at once."""
    db_session.execute(select(Workspace.id).where(Workspace.id == workspace_id).with_for_update())


@workspaces_bp.get("")
@login_required
def list_workspaces():
    rows = workspaces_for_user(db_session, current_user())
    return jsonify(workspaces=[workspace_json(w, r) for w, r in rows])


@workspaces_bp.post("")
@login_required
def create_workspace():
    body = parse_body(WorkspaceBody)
    user = current_user()
    workspace = Workspace(name=body.name.strip(), created_by_id=user.id)
    workspace.members.append(WorkspaceMember(user_id=user.id, role=Role.OWNER))
    db_session.add(workspace)
    db_session.flush()
    audit.record("workspace.create", actor=user, workspace_id=workspace.id)
    db_session.commit()
    return jsonify(workspace=workspace_json(workspace, Role.OWNER)), 201


@workspaces_bp.get("/<uuid:workspace_id>")
@login_required
def get_workspace(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    return jsonify(workspace=workspace_json(scope.workspace, scope.role))


@workspaces_bp.patch("/<uuid:workspace_id>")
@login_required
def update_workspace(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    scope.require_workspace_role(Role.OWNER)
    body = parse_body(WorkspaceBody)
    old = scope.workspace.name
    scope.workspace.name = body.name.strip()
    if old != scope.workspace.name:
        audit.record(
            "workspace.rename",
            actor=current_user(),
            workspace_id=workspace_id,
            details={"from": old, "to": scope.workspace.name},
        )
    db_session.commit()
    return jsonify(workspace=workspace_json(scope.workspace, scope.role))


@workspaces_bp.delete("/<uuid:workspace_id>")
@login_required
def delete_workspace(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    scope.require_workspace_role(Role.OWNER)
    audit.record(
        "workspace.delete",
        actor=current_user(),
        workspace_id=workspace_id,
        details={"name": scope.workspace.name},
    )
    db_session.delete(scope.workspace)
    db_session.commit()
    return "", 204


@workspaces_bp.get("/<uuid:workspace_id>/members")
@login_required
def list_members(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    scope.require_workspace_role(Role.VIEWER)
    rows = db_session.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.created_at)
    ).all()
    return jsonify(members=[member_json(m, u) for m, u in rows])


@workspaces_bp.post("/<uuid:workspace_id>/members")
@login_required
def add_member(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    scope.require_workspace_role(Role.OWNER)
    body = parse_body(AddMemberBody)
    user = db_session.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not user.is_active:
        raise NotFound("No account with this email.")
    exists = db_session.scalar(
        select(WorkspaceMember.id).where(
            WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user.id
        )
    )
    if exists:
        raise Conflict("This user is already a member.")
    member = WorkspaceMember(workspace_id=workspace_id, user_id=user.id, role=body.role)
    db_session.add(member)
    audit.record(
        "member.add",
        actor=current_user(),
        workspace_id=workspace_id,
        target=("user", user.id),
        details={"email": user.email, "role": body.role.value},
    )
    db_session.commit()
    return jsonify(member=member_json(member, user)), 201


@workspaces_bp.patch("/<uuid:workspace_id>/members/<uuid:user_id>")
@login_required
def update_member(workspace_id: uuid.UUID, user_id: uuid.UUID):
    scope = scope_for(workspace_id)
    scope.require_workspace_role(Role.OWNER)
    body = parse_body(MemberRoleBody)
    _lock_workspace(workspace_id)
    member = _get_member(workspace_id, user_id)
    demoting_owner = member.role is Role.OWNER and body.role is not Role.OWNER
    if demoting_owner and _owner_count(workspace_id) == 1:
        raise BadRequest("A workspace needs at least one owner.")
    if member.role is not body.role:
        audit.record(
            "member.role",
            actor=current_user(),
            workspace_id=workspace_id,
            target=("user", user_id),
            details={"from": member.role.value, "to": body.role.value},
        )
    member.role = body.role
    db_session.commit()
    return jsonify(member=member_json(member, db_session.get(User, user_id)))


@workspaces_bp.delete("/<uuid:workspace_id>/members/<uuid:user_id>")
@login_required
def remove_member(workspace_id: uuid.UUID, user_id: uuid.UUID):
    scope = scope_for(workspace_id)
    if user_id != current_user().id:
        scope.require_workspace_role(Role.OWNER)
    elif not scope.is_member:
        raise Forbidden("You are not a member of this workspace.")
    _lock_workspace(workspace_id)
    member = _get_member(workspace_id, user_id)
    if member.role is Role.OWNER and _owner_count(workspace_id) == 1:
        raise BadRequest("A workspace needs at least one owner.")
    audit.record(
        "member.leave" if user_id == current_user().id else "member.remove",
        actor=current_user(),
        workspace_id=workspace_id,
        target=("user", user_id),
        details={"role": member.role.value},
    )
    db_session.delete(member)
    # Leaving a workspace also drops the user's page grants there; otherwise they would
    # keep access as a guest.
    db_session.execute(
        delete(PagePermission)
        .where(
            PagePermission.user_id == user_id,
            PagePermission.page_id.in_(select(Page.id).where(Page.workspace_id == workspace_id)),
        )
        .execution_options(synchronize_session=False)
    )
    db_session.commit()
    return "", 204


@workspaces_bp.get("/<uuid:workspace_id>/audit")
@login_required
def audit_log(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    scope.require_workspace_role(Role.OWNER)
    limit = max(1, min(request.args.get("limit", default=50, type=int), 200))
    query = (
        select(AuditEvent, User)
        .outerjoin(User, User.id == AuditEvent.actor_id)
        .where(AuditEvent.workspace_id == workspace_id)
    )
    if before := request.args.get("before"):
        try:
            query = query.where(AuditEvent.created_at < datetime.fromisoformat(before))
        except ValueError as exc:
            raise BadRequest("before must be an ISO 8601 time.") from exc
    if action := request.args.get("action"):
        query = query.where(AuditEvent.action.startswith(action[:64], autoescape=True))
    rows = db_session.execute(
        query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(limit + 1)
    ).all()
    events = [audit.event_json(e, u) for e, u in rows[:limit]]
    return jsonify(events=events, more=len(rows) > limit)
