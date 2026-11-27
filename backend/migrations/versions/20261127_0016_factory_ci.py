"""Factory CI: checks run on every passed build, their history, and auto-fix settings.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid_fk(name: str, target: str, ondelete: str = "SET NULL") -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=True,
    )


def upgrade() -> None:
    op.add_column("factory_projects", _uuid_fk("checks_page_id", "pages.id"))
    op.add_column(
        "factory_projects",
        sa.Column("ci_auto_fix", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "factory_projects",
        sa.Column("ci_fix_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "factory_ci_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "factory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("factory_projects.page_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        _uuid_fk("run_id", "factory_runs.id"),
        sa.Column("stack", sa.String(40), nullable=False),
        sa.Column("sandbox", sa.String(20), nullable=True),
        sa.Column("files_hash", sa.String(64), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("steps", postgresql.JSONB(), nullable=False),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("summary", sa.Text(), nullable=True),
        _uuid_fk("change_id", "factory_changes.id"),
        _uuid_fk("fix_run_id", "factory_runs.id"),
        _uuid_fk("started_by_id", "users.id"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("factory_id", "number", name="uq_factory_ci_runs_factory_number"),
    )
    op.create_index("ix_factory_ci_runs_status", "factory_ci_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_factory_ci_runs_status", table_name="factory_ci_runs")
    op.drop_table("factory_ci_runs")
    op.drop_column("factory_projects", "ci_fix_streak")
    op.drop_column("factory_projects", "ci_auto_fix")
    op.drop_column("factory_projects", "checks_page_id")
