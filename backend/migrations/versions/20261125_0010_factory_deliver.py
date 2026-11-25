"""Factory, stage F4: releases and pushes to git repositories.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("factory_projects", sa.Column("git_remote", sa.String(500), nullable=True))
    op.add_column("factory_projects", sa.Column("git_branch", sa.String(200), nullable=True))
    op.create_table(
        "factory_releases",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("factory_id", sa.UUID(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column("stack", sa.String(40), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("files", postgresql.JSONB(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("changes", postgresql.JSONB(), nullable=False),
        sa.Column("approved_by_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["factory_id"], ["factory_projects.page_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["factory_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("factory_id", "number", name="uq_factory_releases_factory_number"),
    )
    op.create_table(
        "factory_pushes",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("release_id", sa.UUID(), nullable=False),
        sa.Column("remote", sa.String(500), nullable=False),
        sa.Column("branch", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("pushed_by_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["release_id"], ["factory_releases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pushed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_factory_pushes_release_id", "factory_pushes", ["release_id"])


def downgrade() -> None:
    op.drop_index("ix_factory_pushes_release_id", table_name="factory_pushes")
    op.drop_table("factory_pushes")
    op.drop_table("factory_releases")
    op.drop_column("factory_projects", "git_branch")
    op.drop_column("factory_projects", "git_remote")
    op.execute("UPDATE factory_projects SET stage = 'built' WHERE stage = 'released'")
