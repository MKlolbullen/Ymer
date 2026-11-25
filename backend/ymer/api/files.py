"""Files attached to a page. Bytes go to S3; access follows the page.

- ``GET    /api/workspaces/<ws>/pages/<id>/files``         viewer
- ``POST   /api/workspaces/<ws>/pages/<id>/files``         editor; multipart field ``file``
- ``GET    /api/workspaces/<ws>/pages/<id>/files/<fid>``   viewer; adds a short-lived ``url``
- ``DELETE /api/workspaces/<ws>/pages/<id>/files/<fid>``   editor

Documents refer to files by id (an image node stores ``fileId``), never by URL, so each
viewer gets a fresh presigned URL after a permission check.
"""

import logging
import re
import unicodedata
import uuid
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError
from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select
from werkzeug.exceptions import BadGateway, BadRequest, NotFound, RequestEntityTooLarge

from ymer import audit, ratelimit
from ymer.api.workspaces import scope_for
from ymer.app import settings_for
from ymer.auth import current_user, login_required
from ymer.db import db_session
from ymer.extensions import get_public_s3, get_s3
from ymer.files import delete_stored_objects
from ymer.models import File, Page, Role

log = logging.getLogger(__name__)

files_bp = Blueprint(
    "files", __name__, url_prefix="/workspaces/<uuid:workspace_id>/pages/<uuid:page_id>/files"
)

# Served inline; everything else downloads. SVG is left out on purpose: it can carry script.
INLINE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/avif",
    "application/pdf",
}
URL_TTL = 5 * 60
_CONTENT_TYPE = re.compile(r"^[\w.+-]+/[\w.+-]+$")


def file_json(file: File, *, url: str | None = None) -> dict:
    body = {
        "id": str(file.id),
        "page_id": str(file.page_id),
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": file.size_bytes,
        "created_at": file.created_at.isoformat() if file.created_at else None,
    }
    if url is not None:
        body["url"] = url
    return body


def clean_filename(name: str | None) -> str:
    """Keep a display-safe file name: no paths, control characters or empty names."""
    name = unicodedata.normalize("NFC", name or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip(" .")
    return name[:255] or "file"


def _content_type(value: str | None) -> str:
    value = (value or "").split(";", 1)[0].strip().lower()
    return value if _CONTENT_TYPE.match(value) else "application/octet-stream"


def _get_file(page: Page, file_id: uuid.UUID) -> File:
    file = db_session.scalar(select(File).where(File.id == file_id, File.page_id == page.id))
    if file is None:
        raise NotFound("File not found.")
    return file


def presigned_url(file: File) -> str:
    disposition = "inline" if file.content_type in INLINE_TYPES else "attachment"
    return get_public_s3().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings_for(current_app).s3_bucket,
            "Key": file.storage_key,
            "ResponseContentType": file.content_type,
            "ResponseContentDisposition": f"{disposition}; filename*=UTF-8''{quote(file.filename)}",
        },
        ExpiresIn=URL_TTL,
    )


@files_bp.get("")
@login_required
def list_files(workspace_id: uuid.UUID, page_id: uuid.UUID):
    page = scope_for(workspace_id).get_page(page_id)
    files = db_session.scalars(
        select(File).where(File.page_id == page.id).order_by(File.created_at)
    ).all()
    return jsonify(files=[file_json(f) for f in files])


@files_bp.post("")
@login_required
def upload_file(workspace_id: uuid.UUID, page_id: uuid.UUID):
    page = scope_for(workspace_id).get_page(page_id, Role.EDITOR)
    upload = request.files.get("file")
    if upload is None:
        raise BadRequest("Send the file in a multipart field named 'file'.")
    stream = upload.stream
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    limit = settings_for(current_app).max_upload_bytes
    if size > limit:
        raise RequestEntityTooLarge(f"Files can be at most {limit // (1024 * 1024)} MB.")
    if size == 0:
        raise BadRequest("The file is empty.")

    file_id = uuid.uuid4()
    file = File(
        id=file_id,
        workspace_id=page.workspace_id,
        page_id=page.id,
        storage_key=f"workspaces/{page.workspace_id}/files/{file_id}",
        filename=clean_filename(upload.filename),
        content_type=_content_type(upload.mimetype),
        size_bytes=size,
        uploaded_by_id=current_user().id,
    )
    ratelimit.hit(
        "upload",
        str(current_user().id),
        settings_for(current_app).rate_limit_uploads,
        60,
        "Too many uploads. Wait a minute and try again.",
    )
    try:
        get_s3().upload_fileobj(
            stream,
            settings_for(current_app).s3_bucket,
            file.storage_key,
            ExtraArgs={"ContentType": file.content_type},
        )
    except (BotoCoreError, ClientError) as exc:
        log.exception("Upload to object storage failed")
        raise BadGateway("File storage is not reachable right now. Try again shortly.") from exc
    db_session.add(file)
    audit.record(
        "file.upload",
        actor=current_user(),
        workspace_id=page.workspace_id,
        target=("file", file.id),
        details={"page_id": str(page.id), "filename": file.filename, "size": size},
    )
    db_session.commit()
    return jsonify(file=file_json(file, url=presigned_url(file))), 201


@files_bp.get("/<uuid:file_id>")
@login_required
def get_file(workspace_id: uuid.UUID, page_id: uuid.UUID, file_id: uuid.UUID):
    page = scope_for(workspace_id).get_page(page_id)
    file = _get_file(page, file_id)
    return jsonify(file=file_json(file, url=presigned_url(file)))


@files_bp.delete("/<uuid:file_id>")
@login_required
def delete_file(workspace_id: uuid.UUID, page_id: uuid.UUID, file_id: uuid.UUID):
    page = scope_for(workspace_id).get_page(page_id, Role.EDITOR)
    file = _get_file(page, file_id)
    audit.record(
        "file.delete",
        actor=current_user(),
        workspace_id=page.workspace_id,
        target=("file", file.id),
        details={"page_id": str(page.id), "filename": file.filename},
    )
    key = file.storage_key
    db_session.delete(file)
    db_session.commit()
    # The row goes first: a leftover object is only wasted space, but a row whose object
    # is gone would be a broken file.
    delete_stored_objects([key])
    return "", 204
