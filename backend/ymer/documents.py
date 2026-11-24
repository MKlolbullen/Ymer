"""Storing document content and taking version snapshots.

The collab service owns the live Yjs document while anyone has it open and saves the
merged state here (debounced). Each save may also take an automatic snapshot, so the
version history fills in without anyone pressing "Save version".
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ymer.models import DocumentContent, DocumentVersion


def latest_version(session: Session, page_id: uuid.UUID) -> DocumentVersion | None:
    return session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.page_id == page_id)
        .order_by(DocumentVersion.created_at.desc())
        .limit(1)
    )


def save_state(
    session: Session,
    page_id: uuid.UUID,
    state: bytes,
    *,
    user_id: uuid.UUID | None,
    version_interval: int,
) -> DocumentVersion | None:
    """Store ``state`` as the page's current content.

    Returns the automatic snapshot taken alongside, if one was due: when the page has no
    versions yet, or the newest is older than ``version_interval`` seconds and the content
    changed since. The caller commits.
    """
    content = session.get(DocumentContent, page_id, with_for_update=True)
    if content is None:
        content = DocumentContent(page_id=page_id, state=state)
        session.add(content)
    content.state = state
    content.updated_by_id = user_id

    newest = latest_version(session, page_id)
    due = newest is None or (
        datetime.now(UTC) - newest.created_at >= timedelta(seconds=version_interval)
        and newest.state != state
    )
    if not due:
        return None
    version = DocumentVersion(page_id=page_id, state=state, created_by_id=user_id)
    session.add(version)
    return version
