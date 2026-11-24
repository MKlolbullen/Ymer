"""Semantic search over ``ai_chunks``, limited to what the user may see.

The candidate set is the pages :meth:`WorkspaceScope.visible_pages` returns for the user,
so a chunk of a page they cannot open (or a row of a database they cannot open) is never
scored, returned, or shown to a model on their behalf.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select

from ymer.ai.providers import Embedder
from ymer.models import AiChunk, Page, Role
from ymer.permissions import WorkspaceScope

# Cosine distance above which a chunk is not considered related at all.
MAX_DISTANCE = 0.9


@dataclass
class Hit:
    page: Page
    role: Role
    row_id: uuid.UUID | None
    content: str
    score: float  # 1 - cosine distance; higher is closer


def semantic_search(
    scope: WorkspaceScope, embedder: Embedder, query: str, *, limit: int = 8
) -> list[Hit]:
    """The chunks closest to ``query`` among the pages the user can see.

    At most one hit per page (or per database row), best first.
    """
    visible = {page.id: (page, role) for page, role in scope.visible_pages()}
    if not visible or not query.strip():
        return []
    [vector] = embedder.embed([query])
    distance = AiChunk.embedding.cosine_distance(vector)
    rows = scope.session.execute(
        select(AiChunk.page_id, AiChunk.row_id, AiChunk.content, distance.label("distance"))
        .where(
            AiChunk.workspace_id == scope.workspace.id,
            AiChunk.model == embedder.model,
            AiChunk.page_id.in_(visible),
        )
        .order_by(distance)
        .limit(limit * 4)
    ).all()
    hits: list[Hit] = []
    seen: set[tuple[uuid.UUID, uuid.UUID | None]] = set()
    for page_id, row_id, content, dist in rows:
        if dist is None or dist > MAX_DISTANCE or (page_id, row_id) in seen:
            continue
        seen.add((page_id, row_id))
        page, role = visible[page_id]
        hits.append(Hit(page=page, role=role, row_id=row_id, content=content, score=1 - dist))
        if len(hits) == limit:
            break
    return hits
