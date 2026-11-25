"""The Factory, stage F4: review a build, release it, and deliver it.

Routes, under ``/api/workspaces/<ws>/factories/<factory page id>``:

- ``GET    /releases``                      the releases, newest first
- ``POST   /releases``                      {notes?}: approve the latest passed build as a release
- ``GET    /releases/<n>/download``         the release as a zip, with packaging files
- ``POST   /releases/<n>/preview``          a short-lived link to open a web app's release
- ``POST   /releases/<n>/push``             {remote, branch, token?}: push it to a git repository

and ``GET /api/factory-preview/<token>/<path>``, which serves a previewed release's files
to anyone holding the link while it lasts (an iframe cannot send the login header). The
files are served under a ``sandbox`` content security policy, so the app runs in an
opaque origin and cannot read Ymer's storage or call its API as the viewer.

Reading needs viewer access to the factory; releasing and pushing need editor.
"""

import mimetypes
import uuid

from flask import Blueprint, Response, current_app, jsonify, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from sqlalchemy import select
from werkzeug.exceptions import Conflict, NotFound, UnprocessableEntity

from ymer import audit, ratelimit
from ymer.api.factory import _active_run, _factory, factory_json, git_hosts
from ymer.api.validation import parse_body
from ymer.api.workspaces import scope_for
from ymer.app import settings_for
from ymer.auth import current_user, login_required
from ymer.db import db_session
from ymer.factory.git import GitError, check_branch, check_remote, push_files
from ymer.factory.package import package_files, slug, zip_bytes
from ymer.factory.releases import (
    can_preview,
    create_release,
    current_files,
    latest_release,
    push_json,
    release_json,
    release_requests,
)
from ymer.models import (
    FactoryProject,
    FactoryPush,
    FactoryRelease,
    FactoryRun,
    FactoryStage,
    Page,
    Role,
    RunStatus,
    User,
)
from ymer.permissions import WorkspaceScope

deliver_bp = Blueprint(
    "factory_deliver",
    __name__,
    url_prefix="/workspaces/<uuid:workspace_id>/factories/<uuid:page_id>",
)
preview_bp = Blueprint("factory_preview", __name__, url_prefix="/factory-preview")

PREVIEW_SALT = "ymer.factory-preview"
# The previewed app runs as an opaque origin: it may run scripts and submit forms to
# itself, but cannot reach Ymer's storage, cookies or API, open pop-ups, or navigate the
# page around it. It may load its own files and inline styles and scripts only.
PREVIEW_CSP = (
    "sandbox allow-scripts allow-forms allow-modals; "
    "default-src 'self' data: blob:; "
    "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; connect-src 'none'; frame-ancestors 'self'; "
    "base-uri 'none'; form-action 'none'"
)


def _release(project: FactoryProject, number: int) -> FactoryRelease:
    release = db_session.scalars(
        select(FactoryRelease).where(
            FactoryRelease.factory_id == project.page_id, FactoryRelease.number == number
        )
    ).first()
    if release is None:
        raise NotFound("No such release.")
    return release


def _delivered_files(page: Page, release: FactoryRelease) -> dict[str, str]:
    return package_files(
        release.files,
        app=page.title,
        number=release.number,
        stack=release.stack,
        notes=release.notes,
        when=release.created_at,
        requests=[f"#{c.number} {c.title}" for c in release_requests(db_session, release)],
    )


@deliver_bp.get("/releases")
@login_required
def list_releases(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    _page, project = _factory(scope, page_id, Role.VIEWER)
    releases = db_session.scalars(
        select(FactoryRelease)
        .where(FactoryRelease.factory_id == project.page_id)
        .order_by(FactoryRelease.number.desc())
        .limit(50)
    ).all()
    return jsonify(releases=[release_json(db_session, r) for r in releases])


class ReleaseBody(BaseModel):
    notes: str = Field(default="", max_length=5000)


@deliver_bp.post("/releases")
@login_required
def release(workspace_id: uuid.UUID, page_id: uuid.UUID):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    body = parse_body(ReleaseBody)
    if _active_run(project) is not None:
        raise Conflict("A build is running. Release once it has finished.")
    run = db_session.scalars(
        select(FactoryRun)
        .where(FactoryRun.factory_id == project.page_id)
        .order_by(FactoryRun.created_at.desc())
        .limit(1)
    ).first()
    if run is None or run.status != RunStatus.PASSED.value:
        raise Conflict("Only a build whose tests passed can be released. Build it first.")
    files = current_files(db_session, project.page_id)
    if not files:
        raise Conflict("The build has not written any files yet.")
    previous = latest_release(db_session, project.page_id)
    if previous is not None and previous.files == files:
        raise Conflict(f"Nothing changed since release {previous.number}.")
    created = create_release(
        db_session, project, run_id=run.id, notes=body.notes, user_id=current_user().id
    )
    if project.stage in (FactoryStage.BUILT.value, FactoryStage.PLANNED.value):
        project.stage = FactoryStage.RELEASED.value
    db_session.flush()
    audit.record(
        "factory.release",
        actor=current_user(),
        workspace_id=workspace_id,
        target=("page", page.id),
        details={"release": created.number, "run_id": str(run.id)},
    )
    data = factory_json(scope, page, project)
    out = release_json(db_session, created)
    db_session.commit()
    return jsonify(release=out, factory=data), 201


@deliver_bp.get("/releases/<int:number>/download")
@login_required
def download(workspace_id: uuid.UUID, page_id: uuid.UUID, number: int):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.VIEWER)
    rel = _release(project, number)
    folder = f"{slug(page.title)}-release-{rel.number}"
    data = zip_bytes(_delivered_files(page, rel), folder)
    return Response(
        data,
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{folder}.zip"',
            "Cache-Control": "private, no-store",
        },
    )


# --- preview ----------------------------------------------------------------------------


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings_for(current_app).secret_key, salt=PREVIEW_SALT)


@deliver_bp.post("/releases/<int:number>/preview")
@login_required
def preview_link(workspace_id: uuid.UUID, page_id: uuid.UUID, number: int):
    scope = scope_for(workspace_id)
    _page, project = _factory(scope, page_id, Role.VIEWER)
    rel = _release(project, number)
    if not can_preview(rel):
        raise UnprocessableEntity(
            "Only web apps with an index.html can be previewed here. Download the release "
            "to run it."
        )
    token = _serializer().dumps(
        {"r": str(rel.id), "u": str(current_user().id), "w": str(workspace_id)}
    )
    minutes = settings_for(current_app).factory_preview_minutes
    url = url_for("api.factory_preview.serve", token=token, path="index.html")
    return jsonify(url=url, expires_in=minutes * 60)


def _preview_release(token: str) -> FactoryRelease:
    """The release a preview link points at, if the link is valid and its creator can
    still open the factory."""
    minutes = settings_for(current_app).factory_preview_minutes
    try:
        data = _serializer().loads(token, max_age=minutes * 60)
        release_id, user_id, workspace_id = (uuid.UUID(data[k]) for k in ("r", "u", "w"))
    except SignatureExpired as exc:
        raise NotFound("This preview link has expired. Open the preview again.") from exc
    except (BadSignature, KeyError, TypeError, ValueError) as exc:
        raise NotFound("No such preview.") from exc
    rel = db_session.get(FactoryRelease, release_id)
    user = db_session.get(User, user_id)
    if rel is None or user is None:
        raise NotFound("No such preview.")
    scope = WorkspaceScope.load(db_session, user, workspace_id)
    scope.get_page(rel.factory_id, Role.VIEWER)  # 404 when they lost access
    return rel


@preview_bp.get("/<token>/<path:path>")
def serve(token: str, path: str):
    rel = _preview_release(token)
    content = rel.files.get(path)
    if content is None and (path.endswith("/") or "." not in path.rsplit("/", 1)[-1]):
        content = rel.files.get(path.rstrip("/") + "/index.html")
        path = path.rstrip("/") + "/index.html"
    if content is None:
        raise NotFound("No such file in this release.")
    mimetype = mimetypes.guess_type(path)[0] or "text/plain"
    if path.endswith((".mjs", ".js")):
        mimetype = "text/javascript"
    response = Response(content, mimetype=mimetype)
    response.headers["Content-Security-Policy"] = PREVIEW_CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "private, no-store"
    # The sandboxed page has an opaque origin, so its own module scripts and styles are
    # cross-origin requests to us. The link itself is the credential, so any origin may
    # read these files; the browser sends no Ymer login with them.
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


# --- git ----------------------------------------------------------------------------------


class PushBody(BaseModel):
    remote: str = Field(min_length=1, max_length=500)
    branch: str = Field(default="main", min_length=1, max_length=200)
    token: str = Field(default="", max_length=1000)


@deliver_bp.post("/releases/<int:number>/push")
@login_required
def push(workspace_id: uuid.UUID, page_id: uuid.UUID, number: int):
    scope = scope_for(workspace_id)
    page, project = _factory(scope, page_id, Role.EDITOR)
    rel = _release(project, number)
    body = parse_body(PushBody)
    settings = settings_for(current_app)
    try:
        remote = check_remote(body.remote, git_hosts(settings), settings.factory_git_allow_local)
        branch = check_branch(body.branch)
    except ValueError as exc:
        raise UnprocessableEntity(str(exc)) from exc
    user = current_user()
    ratelimit.hit(
        "git-push",
        str(user.id),
        settings.rate_limit_git_push,
        3600,
        "Too many pushes in the last hour. Try again later.",
    )
    record = FactoryPush(
        release_id=rel.id, remote=remote, branch=branch, status="failed", pushed_by_id=user.id
    )
    try:
        result = push_files(
            _delivered_files(page, rel),
            remote=remote,
            branch=branch,
            token=body.token,
            message=f"{page.title or 'App'}: release {rel.number}\n\n"
            f"{rel.notes or 'Built and released with Ymer Factory.'}\n\n"
            f"Released by {user.display_name} in Ymer Factory.",
            author=("Ymer Factory", "factory@ymer.invalid"),
            timeout=settings.factory_git_timeout,
            ca_bundle=settings.factory_git_ca_bundle,
        )
        record.status = "pushed" if result.changed else "unchanged"
        record.commit_sha = result.commit_sha
    except GitError as exc:
        record.error = str(exc)
    db_session.add(record)
    project.git_remote, project.git_branch = remote, branch
    audit.record(
        "factory.push",
        actor=user,
        workspace_id=workspace_id,
        target=("page", page.id),
        details={
            "release": rel.number,
            "remote": remote,
            "branch": branch,
            "status": record.status,
            "commit": record.commit_sha,
        },
    )
    db_session.flush()
    out = push_json(db_session, record)
    db_session.commit()
    return jsonify(push=out, release=release_json(db_session, rel)), (
        200 if record.status != "failed" else 502
    )
