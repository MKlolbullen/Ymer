"""Factory F5: change requests, spec snapshots and a monthly budget per workspace.

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("factory_sources", sa.Column("seen_text", sa.Text(), nullable=True))
    op.add_column("factory_sources", sa.Column("seen_hash", sa.String(64), nullable=True))
    op.add_column(
        "factory_sources", sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("workspaces", sa.Column("factory_monthly_tokens", sa.Integer(), nullable=True))
    op.create_table(
        "factory_changes",
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
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("tasks", postgresql.JSONB(), nullable=False),
        sa.Column(
            "release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("factory_releases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("factory_id", "number", name="uq_factory_changes_factory_number"),
    )


def downgrade() -> None:
    op.drop_table("factory_changes")
    op.drop_column("workspaces", "factory_monthly_tokens")
    op.drop_column("factory_sources", "seen_at")
    op.drop_column("factory_sources", "seen_hash")
    op.drop_column("factory_sources", "seen_text")
