"""The monthly Factory budget of a workspace: tokens all its builds may use per month.

Each build already has its own caps (steps and tokens per run). On top of that, a
workspace has a monthly token limit, counted over the build runs of every factory in it
since the first of the month (UTC). The server sets the default
(``YMER_FACTORY_WORKSPACE_MONTHLY_TOKENS``) and a workspace owner can set its own; 0
means no limit. A build that starts gets at most what is left, so a run stops and says
so when the month's budget runs out. Brief, architecture and planning calls are single
requests and are not counted.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ymer.config import Settings
from ymer.models import FactoryRun, Page, Workspace

MAX_MONTHLY_TOKENS = 1_000_000_000


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def monthly_limit(workspace: Workspace, settings: Settings) -> int:
    """Tokens per month; 0 means no limit."""
    if workspace.factory_monthly_tokens is not None:
        return workspace.factory_monthly_tokens
    return settings.factory_workspace_monthly_tokens


def used_this_month(session: Session, workspace_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.coalesce(func.sum(FactoryRun.tokens), 0))
            .join(Page, Page.id == FactoryRun.factory_id)
            .where(Page.workspace_id == workspace_id, FactoryRun.created_at >= month_start())
        )
        or 0
    )


def remaining(session: Session, workspace: Workspace, settings: Settings) -> int | None:
    """Tokens left this month, or None when there is no limit."""
    limit = monthly_limit(workspace, settings)
    if limit <= 0:
        return None
    return max(0, limit - used_this_month(session, workspace.id))


def budget_json(
    session: Session, workspace: Workspace, settings: Settings, *, can_edit: bool
) -> dict:
    limit = monthly_limit(workspace, settings)
    used = used_this_month(session, workspace.id)
    return {
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used) if limit > 0 else None,
        "period_start": month_start().isoformat(),
        "custom": workspace.factory_monthly_tokens is not None,
        "default_limit": settings.factory_workspace_monthly_tokens,
        "can_edit": can_edit,
    }
