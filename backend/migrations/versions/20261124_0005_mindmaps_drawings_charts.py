"""Mind maps, drawings and charts: a ``drawing`` page type and a ``chart`` view type.

Mind maps already had a page type (``mindmap``, from 0002). Their content, like a
drawing's, is a Yjs document stored in ``document_content`` / ``document_versions``,
so no new tables are needed.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Postgres 12+ allows this inside a transaction as long as the new value is not
    # used in the same transaction.
    op.execute("ALTER TYPE page_type ADD VALUE IF NOT EXISTS 'drawing'")
    op.execute("ALTER TYPE view_type ADD VALUE IF NOT EXISTS 'chart'")


def downgrade() -> None:
    # Enum values cannot be dropped: delete what uses them, then rebuild each type.
    op.execute("DELETE FROM pages WHERE type = 'drawing'")
    op.execute("DELETE FROM database_views WHERE type = 'chart'")
    op.execute("ALTER TYPE page_type RENAME TO page_type_old")
    op.execute("CREATE TYPE page_type AS ENUM ('document', 'database', 'mindmap', 'folder')")
    op.execute("ALTER TABLE pages ALTER COLUMN type TYPE page_type USING type::text::page_type")
    op.execute("DROP TYPE page_type_old")
    op.execute("ALTER TYPE view_type RENAME TO view_type_old")
    op.execute("CREATE TYPE view_type AS ENUM ('table', 'board')")
    op.execute(
        "ALTER TABLE database_views ALTER COLUMN type TYPE view_type USING type::text::view_type"
    )
    op.execute("DROP TYPE view_type_old")
