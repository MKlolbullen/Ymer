"""Releases: an editor reviews a passed build and approves it as release N.

A release is a frozen copy of the app's files at that moment, so later builds can keep
changing the working files while what was approved stays downloadable, previewable and
pushable. Each release records what changed against the one before it.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ymer.factory.package import changes_between
from ymer.factory.stacks import STACKS
from ymer.models import FactoryFile, FactoryProject, FactoryPush, FactoryRelease, User

# Stacks whose release can be opened in the browser as it is.
PREVIEWABLE = {"static-web"}


def current_files(session: Session, factory_id: uuid.UUID) -> dict[str, str]:
    return {
        f.path: f.content
        for f in session.scalars(select(FactoryFile).where(FactoryFile.factory_id == factory_id))
    }


def latest_release(session: Session, factory_id: uuid.UUID) -> FactoryRelease | None:
    return session.scalars(
        select(FactoryRelease)
        .where(FactoryRelease.factory_id == factory_id)
        .order_by(FactoryRelease.number.desc())
        .limit(1)
    ).first()


def can_preview(release: FactoryRelease) -> bool:
    return release.stack in PREVIEWABLE and "index.html" in release.files


def create_release(
    session: Session,
    project: FactoryProject,
    *,
    run_id: uuid.UUID | None,
    notes: str | None,
    user_id: uuid.UUID,
) -> FactoryRelease:
    files = current_files(session, project.page_id)
    previous = latest_release(session, project.page_id)
    number = (previous.number if previous else 0) + 1
    release = FactoryRelease(
        factory_id=project.page_id,
        number=number,
        run_id=run_id,
        stack=project.stack if project.stack in STACKS else "flask-react",
        notes=(notes or "").strip() or None,
        files=files,
        file_count=len(files),
        size=sum(len(c) for c in files.values()),
        changes=changes_between(previous.files if previous else None, files),
        approved_by_id=user_id,
    )
    session.add(release)
    session.flush()
    return release


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _person(session: Session, user_id: uuid.UUID | None) -> dict | None:
    user = session.get(User, user_id) if user_id else None
    return {"id": str(user.id), "display_name": user.display_name} if user else None


def push_json(session: Session, push: FactoryPush) -> dict:
    return {
        "id": str(push.id),
        "remote": push.remote,
        "branch": push.branch,
        "status": push.status,
        "commit_sha": push.commit_sha,
        "error": push.error,
        "pushed_by": _person(session, push.pushed_by_id),
        "created_at": _iso(push.created_at),
    }


def release_json(session: Session, release: FactoryRelease) -> dict:
    pushes = session.scalars(
        select(FactoryPush)
        .where(FactoryPush.release_id == release.id)
        .order_by(FactoryPush.created_at.desc())
        .limit(10)
    ).all()
    return {
        "id": str(release.id),
        "number": release.number,
        "run_id": str(release.run_id) if release.run_id else None,
        "stack": release.stack,
        "notes": release.notes,
        "file_count": release.file_count,
        "size": release.size,
        "changes": release.changes,
        "approved_by": _person(session, release.approved_by_id),
        "created_at": _iso(release.created_at),
        "preview": can_preview(release),
        "pushes": [push_json(session, p) for p in pushes],
    }


def delivery_json(session: Session, project: FactoryProject, git_hosts: list[str]) -> dict:
    latest = latest_release(session, project.page_id)
    count = session.scalar(select(func.count()).where(FactoryRelease.factory_id == project.page_id))
    files = current_files(session, project.page_id)
    pending = changes_between(latest.files if latest else None, files) if files else None
    return {
        "latest_release": release_json(session, latest) if latest else None,
        "release_count": count,
        # What the working files would change if released now (None when there are none).
        "unreleased": pending,
        "git_remote": project.git_remote,
        "git_branch": project.git_branch,
        "git_hosts": git_hosts,
    }
