"""The audit log: who did what, from where.

Call :func:`record` next to the change it describes, before the request commits, so the
event and the change are saved (or rolled back) together. For outcomes that do not
otherwise commit, such as a failed sign-in, use ``commit=True``.

Never put secrets in ``details``: no passwords, tokens or API keys.
"""

import uuid
from datetime import UTC, datetime, timedelta

from flask import has_request_context, request
from sqlalchemy import delete

from ymer.db import db_session
from ymer.models import AuditEvent, User


def record(
    action: str,
    *,
    actor: User | uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
    target: tuple[str, object] | None = None,
    details: dict | None = None,
    commit: bool = False,
) -> AuditEvent:
    actor_id = actor.id if isinstance(actor, User) else actor
    ip = user_agent = None
    if has_request_context():
        from ymer.ratelimit import client_ip

        ip = client_ip()[:64]
        user_agent = (request.headers.get("User-Agent") or "")[:300] or None
    event = AuditEvent(
        actor_id=actor_id,
        workspace_id=workspace_id,
        action=action,
        target_type=target[0] if target else None,
        target_id=str(target[1])[:64] if target else None,
        ip=ip,
        user_agent=user_agent,
        details=details or {},
    )
    db_session.add(event)
    if commit:
        db_session.commit()
    return event


def prune(days: int) -> int:
    """Delete events older than ``days``; returns how many."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    result = db_session.execute(delete(AuditEvent).where(AuditEvent.created_at < cutoff))
    db_session.commit()
    return result.rowcount or 0


def event_json(event: AuditEvent, actor: User | None) -> dict:
    return {
        "id": str(event.id),
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "action": event.action,
        "actor": (
            {"id": str(actor.id), "display_name": actor.display_name, "email": actor.email}
            if actor
            else None
        ),
        "workspace_id": str(event.workspace_id) if event.workspace_id else None,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "ip": event.ip,
        "user_agent": event.user_agent,
        "details": event.details,
    }
