"""Factory, stage F3: build runs, their log, and the files they write.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=None if nullable else sa.text("now()"),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "factory_runs",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("factory_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("started_by_id", sa.UUID(), nullable=True),
        sa.Column(
            "context", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("steps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tasks_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tasks_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("started_at", nullable=True),
        _ts("finished_at", nullable=True),
        sa.ForeignKeyConstraint(["factory_id"], ["factory_projects.page_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["started_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_factory_runs_factory_id", "factory_runs", ["factory_id"])
    op.create_index("ix_factory_runs_status", "factory_runs", ["status"])
    op.create_table(
        "factory_run_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.ForeignKeyConstraint(["run_id"], ["factory_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "seq", name="uq_factory_run_events_run_seq"),
    )
    op.create_table(
        "factory_files",
        sa.Column("factory_id", sa.UUID(), nullable=False),
        sa.Column("path", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(["factory_id"], ["factory_projects.page_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["factory_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("factory_id", "path"),
    )


def downgrade() -> None:
    op.drop_table("factory_files")
    op.drop_table("factory_run_events")
    op.drop_index("ix_factory_runs_status", table_name="factory_runs")
    op.drop_index("ix_factory_runs_factory_id", table_name="factory_runs")
    op.drop_table("factory_runs")
    op.execute("UPDATE factory_projects SET stage = 'planned' WHERE stage = 'built'")
