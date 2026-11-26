"""Templates: pages saved as templates for their workspace.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pages",
        sa.Column("is_template", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index(
        "ix_pages_workspace_id_templates",
        "pages",
        ["workspace_id"],
        postgresql_where=sa.text("is_template"),
    )


def downgrade() -> None:
    op.drop_index("ix_pages_workspace_id_templates", table_name="pages")
    op.drop_column("pages", "is_template")
