"""Full-text search across a workspace.

- ``GET /api/workspaces/<ws>/search?q=<text>[&limit=20]``

Searches page titles and document text (``pages.search_vector``) and database rows
(``database_rows.search_vector``) with Postgres full-text search, plus trigram
similarity on page titles so small typos still find a page. Only pages the user can see,
and rows of databases they can see, are returned.

The ``simple`` text search configuration is used (no stemming, any language) and every
word is matched as a prefix, so "plan" finds "planning". In ``snippet`` the matched words
are wrapped in ``\\u0002`` and ``\\u0003``, which the client turns into highlights.
"""

import re
import uuid

from flask import Blueprint, jsonify, request
from sqlalchemy import func, or_, select

from ymer.api.workspaces import scope_for
from ymer.auth import login_required
from ymer.db import db_session
from ymer.models import DatabaseProperty, DatabaseRow, Page, PageType, PropertyType

search_bp = Blueprint("search", __name__, url_prefix="/workspaces/<uuid:workspace_id>/search")

HEADLINE = "StartSel=\u0002, StopSel=\u0003, MaxWords=24, MinWords=10, MaxFragments=1"


def prefix_query(text: str) -> str | None:
    """A to_tsquery string matching every word of ``text`` as a prefix, or None."""
    words = re.findall(r"\w+", text.lower())[:8]
    return " & ".join(f"{w}:*" for w in words) or None


@search_bp.get("")
@login_required
def search(workspace_id: uuid.UUID):
    scope = scope_for(workspace_id)
    q = (request.args.get("q") or "").strip()[:200]
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 50))
    except ValueError:
        limit = 20
    query = prefix_query(q)
    if not query:
        return jsonify(results=[])

    visible = {page.id: (page, role) for page, role in scope.visible_pages()}
    if not visible:
        return jsonify(results=[])
    tsq = func.to_tsquery("simple", query)

    rank = func.ts_rank(Page.search_vector, tsq) + func.similarity(Page.title, q)
    page_rows = db_session.execute(
        select(
            Page.id,
            rank.label("rank"),
            func.ts_headline("simple", Page.content_text, tsq, HEADLINE).label("snippet"),
        )
        .where(
            Page.workspace_id == workspace_id,
            Page.id.in_(visible),
            or_(Page.search_vector.op("@@")(tsq), Page.title.op("%")(q)),
        )
        .order_by(rank.desc())
        .limit(limit)
    ).all()

    results = []
    for page_id, score, snippet in page_rows:
        page, role = visible[page_id]
        results.append(
            {
                "kind": "page",
                "page_id": str(page.id),
                "type": page.type.value,
                "title": page.title,
                "snippet": snippet if "\u0002" in (snippet or "") else None,
                "my_role": role.value,
                "score": float(score),
            }
        )

    databases = [pid for pid, (p, _) in visible.items() if p.type is PageType.DATABASE]
    if databases:
        title_props = {
            db_id: str(prop_id)
            for db_id, prop_id in db_session.execute(
                select(DatabaseProperty.database_id, DatabaseProperty.id).where(
                    DatabaseProperty.database_id.in_(databases),
                    DatabaseProperty.type == PropertyType.TITLE,
                )
            )
        }
        row_rank = func.ts_rank(DatabaseRow.search_vector, tsq)
        rows = db_session.execute(
            select(
                DatabaseRow,
                row_rank.label("rank"),
                func.ts_headline("simple", DatabaseRow.search_text, tsq, HEADLINE).label("snippet"),
            )
            .where(
                DatabaseRow.workspace_id == workspace_id,
                DatabaseRow.database_id.in_(databases),
                DatabaseRow.search_vector.op("@@")(tsq),
            )
            .order_by(row_rank.desc())
            .limit(limit)
        ).all()
        for row, score, snippet in rows:
            database = visible[row.database_id][0]
            key = title_props.get(row.database_id)
            results.append(
                {
                    "kind": "row",
                    "page_id": str(database.id),
                    "row_id": str(row.id),
                    "type": "row",
                    "title": (row.values.get(key) if key else None) or "",
                    "database_title": database.title,
                    "snippet": snippet,
                    "my_role": visible[row.database_id][1].value,
                    "score": float(score),
                }
            )

    results.sort(key=lambda r: r["score"], reverse=True)
    return jsonify(results=results[:limit])
