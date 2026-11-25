"""Factory, stage F2: the stack and the architecture, system map and task pages.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAGE_COLUMNS = ("architecture_page_id", "map_page_id", "tasks_page_id")


def upgrade() -> None:
    op.add_column("factory_projects", sa.Column("stack", sa.String(40), nullable=True))
    for column in PAGE_COLUMNS:
        op.add_column("factory_projects", sa.Column(column, sa.UUID(), nullable=True))
        op.create_foreign_key(
            f"fk_factory_projects_{column}",
            "factory_projects",
            "pages",
            [column],
            ["id"],
            ondelete="SET NULL",
        )
    op.add_column(
        "factory_projects",
        sa.Column("architecture_generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "factory_projects", sa.Column("plan_approved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("factory_projects", sa.Column("plan_approved_by_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_factory_projects_plan_approved_by_id",
        "factory_projects",
        "users",
        ["plan_approved_by_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_factory_projects_plan_approved_by_id", "factory_projects", type_="foreignkey"
    )
    for column in ("plan_approved_by_id", "plan_approved_at", "architecture_generated_at"):
        op.drop_column("factory_projects", column)
    for column in PAGE_COLUMNS:
        op.drop_constraint(f"fk_factory_projects_{column}", "factory_projects", type_="foreignkey")
        op.drop_column("factory_projects", column)
    op.drop_column("factory_projects", "stack")
    op.execute(
        "UPDATE factory_projects SET stage = 'approved' WHERE stage IN ('architecture', 'planned')"
    )
