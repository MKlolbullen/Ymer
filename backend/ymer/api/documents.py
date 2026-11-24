"""Version history and comments on document pages.

- ``GET    /api/workspaces/<ws>/pages/<id>/versions``          viewer; newest first
- ``POST   /api/workspaces/<ws>/pages/<id>/versions``          editor; snapshot now {label?}
- ``GET    /api/workspaces/<ws>/pages/<id>/versions/<vid>``    viewer; includes base64 ``state``
- ``GET    /api/workspaces/<ws>/pages/<id>/comments``          viewer; all threads, oldest first
- ``POST   /api/workspaces/<ws>/pages/<id>/comments``          commenter
- ``PATCH  /api/workspaces/<ws>/pages/<id>/comments/<cid>``    body: author; resolved: commenter
- ``DELETE /api/workspaces/<ws>/pages/<id>/comments/<cid>``    author, or editor on the page

Restoring a version happens in the editor: the client loads the version's state and
applies its content as a new edit, so everyone connected sees it and nothing is lost.
"""

import base64
import uuid
from datetime import UTC, datetime

from flask import Blueprint, jsonify
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import aliased
from werkzeug.exceptions import BadRequest, Conflict, Forbidden, NotFound

from ymer.api.validation import parse_body
from ymer.api.workspaces import scope_for
from ymer.auth import current_user, login_required
from ymer.db import db_session
from ymer.models import Comment, DocumentContent, DocumentVersion, Page, PageType, Role, User
from ymer.permissions import WorkspaceScope

documents_bp = Blueprint(
    "documents", __name__, url_prefix="/workspaces/<uuid:workspace_id>/pages/<uuid:page_id>"
)


def _document(scope: WorkspaceScope, page_id: uuid.UUID, minimum: Role) -> Page:
    page = scope.get_page(page_id, minimum)
    if page.type is not PageType.DOCUMENT:
        raise NotFound("This page is not a document.")
    return page


def _person(user: User | None) -> dict | None:
    if user is None:
        return None
    return {"id": str(user.id), "display_name": user.display_name}


# --- versions -------------------------------------------------------------------------


class CreateVersionBody(BaseModel):
    label: str | None = Field(default=None, max_length=200)

    @field_validator("label")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


def version_json(version: DocumentVersion, author: User | None, *, state: bool = False) -> dict:
    body = {
        "id": str(version.id),
        "label": version.label,
        "created_at": version.created_at.isoformat(),
        "created_by": _person(author),
        "size_bytes": len(version.state),
    }
    if state:
        body["state"] = base64.b64encode(version.state).decode()
    return body


@documents_bp.get("/versions")
@login_required
def list_versions(workspace_id: uuid.UUID, page_id: uuid.UUID):
    page = _document(scope_for(workspace_id), page_id, Role.VIEWER)
    rows = db_session.execute(
        select(DocumentVersion, User)
        .outerjoin(User, User.id == DocumentVersion.created_by_id)
        .where(DocumentVersion.page_id == page.id)
        .order_by(DocumentVersion.created_at.desc())
        .limit(200)
    ).all()
    return jsonify(versions=[version_json(v, u) for v, u in rows])


@documents_bp.post("/versions")
@login_required
def create_version(workspace_id: uuid.UUID, page_id: uuid.UUID):
    page = _document(scope_for(workspace_id), page_id, Role.EDITOR)
    body = parse_body(CreateVersionBody)
    content = db_session.get(DocumentContent, page.id)
    if content is None:
        raise Conflict("The document has no saved content yet.")
    version = DocumentVersion(
        page_id=page.id, state=content.state, label=body.label, created_by_id=current_user().id
    )
    db_session.add(version)
    db_session.commit()
    return jsonify(version=version_json(version, current_user())), 201


@documents_bp.get("/versions/<uuid:version_id>")
@login_required
def get_version(workspace_id: uuid.UUID, page_id: uuid.UUID, version_id: uuid.UUID):
    page = _document(scope_for(workspace_id), page_id, Role.VIEWER)
    version = db_session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == version_id, DocumentVersion.page_id == page.id
        )
    )
    if version is None:
        raise NotFound("Version not found.")
    author = db_session.get(User, version.created_by_id) if version.created_by_id else None
    return jsonify(version=version_json(version, author, state=True))


# --- comments -------------------------------------------------------------------------


class Anchor(BaseModel):
    """Two Yjs relative positions, encoded by the client (opaque to the server)."""

    from_: str = Field(alias="from", max_length=2000)
    to: str = Field(max_length=2000)


class CreateCommentBody(BaseModel):
    body: str = Field(min_length=1, max_length=10_000)
    thread_id: uuid.UUID | None = None
    anchor: Anchor | None = None
    quote: str | None = Field(default=None, max_length=1000)


class UpdateCommentBody(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=10_000)
    resolved: bool | None = None


def comment_json(comment: Comment, author: User | None, resolver: User | None = None) -> dict:
    return {
        "id": str(comment.id),
        "thread_id": str(comment.thread_id) if comment.thread_id else None,
        "author": _person(author),
        "body": comment.body,
        "anchor": comment.anchor,
        "quote": comment.quote,
        "resolved_at": comment.resolved_at.isoformat() if comment.resolved_at else None,
        "resolved_by": _person(resolver),
        "created_at": comment.created_at.isoformat(),
        "updated_at": comment.updated_at.isoformat(),
    }


def _get_comment(page: Page, comment_id: uuid.UUID) -> Comment:
    comment = db_session.scalar(
        select(Comment).where(Comment.id == comment_id, Comment.page_id == page.id)
    )
    if comment is None:
        raise NotFound("Comment not found.")
    return comment


@documents_bp.get("/comments")
@login_required
def list_comments(workspace_id: uuid.UUID, page_id: uuid.UUID):
    page = _document(scope_for(workspace_id), page_id, Role.VIEWER)
    author, resolver = aliased(User), aliased(User)
    rows = db_session.execute(
        select(Comment, author, resolver)
        .outerjoin(author, author.id == Comment.author_id)
        .outerjoin(resolver, resolver.id == Comment.resolved_by_id)
        .where(Comment.page_id == page.id)
        .order_by(Comment.created_at)
    ).all()
    return jsonify(comments=[comment_json(c, a, r) for c, a, r in rows])


@documents_bp.post("/comments")
@login_required
def create_comment(workspace_id: uuid.UUID, page_id: uuid.UUID):
    page = _document(scope_for(workspace_id), page_id, Role.COMMENTER)
    body = parse_body(CreateCommentBody)
    if body.thread_id is not None:
        root = _get_comment(page, body.thread_id)
        if root.thread_id is not None:
            raise BadRequest("Reply to the thread's first comment.")
        if body.anchor is not None:
            raise BadRequest("Only the first comment of a thread can be anchored.")
    comment = Comment(
        page_id=page.id,
        thread_id=body.thread_id,
        author_id=current_user().id,
        body=body.body.strip(),
        anchor=body.anchor.model_dump(by_alias=True) if body.anchor else None,
        quote=body.quote,
    )
    db_session.add(comment)
    db_session.commit()
    return jsonify(comment=comment_json(comment, current_user())), 201


@documents_bp.patch("/comments/<uuid:comment_id>")
@login_required
def update_comment(workspace_id: uuid.UUID, page_id: uuid.UUID, comment_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _document(scope, page_id, Role.COMMENTER)
    comment = _get_comment(page, comment_id)
    body = parse_body(UpdateCommentBody)
    me = current_user()
    if body.body is not None:
        if comment.author_id != me.id:
            raise Forbidden("Only the author can edit a comment.")
        comment.body = body.body.strip()
    if body.resolved is not None:
        if comment.thread_id is not None:
            raise BadRequest("Resolve the thread's first comment.")
        comment.resolved_at = datetime.now(UTC) if body.resolved else None
        comment.resolved_by_id = me.id if body.resolved else None
    db_session.commit()
    author = db_session.get(User, comment.author_id) if comment.author_id else None
    resolver = db_session.get(User, comment.resolved_by_id) if comment.resolved_by_id else None
    return jsonify(comment=comment_json(comment, author, resolver))


@documents_bp.delete("/comments/<uuid:comment_id>")
@login_required
def delete_comment(workspace_id: uuid.UUID, page_id: uuid.UUID, comment_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page = _document(scope, page_id, Role.COMMENTER)
    comment = _get_comment(page, comment_id)
    is_author = comment.author_id == current_user().id
    if not is_author and not scope.page_role(page).at_least(Role.EDITOR):
        raise Forbidden("Only the author or an editor can delete a comment.")
    db_session.delete(comment)  # replies go with a thread's first comment (FK cascade)
    db_session.commit()
    return "", 204
