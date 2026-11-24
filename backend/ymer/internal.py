"""Internal API for the collab service (``collab/``). Not under ``/api``.

The collab service is the only caller. Each request carries the shared secret in
``X-Ymer-Collab-Secret`` (``YMER_COLLAB_SECRET``). A reverse proxy in front of Flask
should not route ``/internal`` from the internet at all; the secret is a second lock.

Every page type edited live over Yjs (documents, mind maps, drawings; see
``LIVE_PAGE_TYPES``) goes through these routes; the collab service does not care which.

- ``POST /internal/collab/authorize``      {token, document} -> who connects and can they write
- ``GET  /internal/collab/documents/<id>`` current Yjs state (204 when there is none yet)
- ``PUT  /internal/collab/documents/<id>`` store Yjs state; ``X-Ymer-User-Id`` = last editor.
  Also refreshes the page's search text and outgoing links (``ymer.content``).
"""

import hmac
import uuid

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import BaseModel
from werkzeug.exceptions import BadRequest, NotFound, Unauthorized

from ymer.api.validation import parse_body
from ymer.app import settings_for
from ymer.auth.current import load_user_from_token
from ymer.content import index_document
from ymer.db import db_session
from ymer.documents import save_state
from ymer.models import LIVE_PAGE_TYPES, DocumentContent, Page, Role, User
from ymer.permissions import WorkspaceScope

internal_bp = Blueprint("internal", __name__, url_prefix="/internal/collab")


@internal_bp.before_request
def _require_collab_secret() -> None:
    expected = settings_for(current_app).collab_secret
    given = request.headers.get("X-Ymer-Collab-Secret", "")
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise Unauthorized("Invalid collab secret.")


class AuthorizeBody(BaseModel):
    token: str
    document: str


def parse_document_name(name: str) -> uuid.UUID:
    """Documents are named by their page id."""
    try:
        return uuid.UUID(name)
    except ValueError as exc:
        raise NotFound("Document not found.") from exc


def _document_page(page_id: uuid.UUID) -> Page:
    page = db_session.get(Page, page_id)
    if page is None or page.type not in LIVE_PAGE_TYPES:
        raise NotFound("Document not found.")
    return page


@internal_bp.post("/authorize")
def authorize():
    body = parse_body(AuthorizeBody)
    user = load_user_from_token(body.token, "access")
    page_id = parse_document_name(body.document)
    page = db_session.get(Page, page_id)
    if page is None:
        raise NotFound("Document not found.")
    scope = WorkspaceScope.load(db_session, user, page.workspace_id)
    page = scope.get_page(page_id)  # 404 unless the user can at least view it
    if page.type not in LIVE_PAGE_TYPES:
        raise NotFound("Document not found.")
    role = scope.page_role(page)
    return jsonify(
        user={"id": str(user.id), "display_name": user.display_name},
        role=role.value,
        page_type=page.type.value,
        read_only=not role.at_least(Role.EDITOR),
    )


@internal_bp.get("/documents/<uuid:page_id>")
def fetch_document(page_id: uuid.UUID):
    _document_page(page_id)
    content = db_session.get(DocumentContent, page_id)
    if content is None:
        return "", 204
    return Response(content.state, mimetype="application/octet-stream")


@internal_bp.put("/documents/<uuid:page_id>")
def store_document(page_id: uuid.UUID):
    page = _document_page(page_id)
    state = request.get_data()
    if not state:
        raise BadRequest("Empty document state.")
    user_header = request.headers.get("X-Ymer-User-Id")
    try:
        user_id = uuid.UUID(user_header) if user_header else None
    except ValueError as exc:
        raise BadRequest("X-Ymer-User-Id must be a UUID.") from exc
    if user_id is not None and db_session.get(User, user_id) is None:
        user_id = None  # deleted since they edited; keep the content, drop attribution
    save_state(
        db_session,
        page_id,
        state,
        user_id=user_id,
        version_interval=settings_for(current_app).version_interval,
    )
    index_document(db_session, page, state)
    db_session.commit()
    return "", 204
