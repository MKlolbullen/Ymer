"""Factory, stage F1: a ``factory`` page type, its sources, questions and brief.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
    )


def upgrade() -> None:
    # Postgres 12+ allows this inside a transaction as long as the new value is not used
    # in the same transaction.
    op.execute("ALTER TYPE page_type ADD VALUE IF NOT EXISTS 'factory'")
    op.create_table(
        "factory_projects",
        sa.Column("page_id", sa.UUID(), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False, server_default="sources"),
        sa.Column("brief_page_id", sa.UUID(), nullable=True),
        sa.Column("brief_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_id", sa.UUID(), nullable=True),
        _now(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["brief_page_id"], ["pages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("page_id"),
    )
    op.create_table(
        "factory_sources",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("factory_id", sa.UUID(), nullable=False),
        sa.Column("page_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("added_by_id", sa.UUID(), nullable=True),
        _now(),
        sa.ForeignKeyConstraint(["factory_id"], ["factory_projects.page_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["added_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("factory_id", "page_id", name="uq_factory_sources_factory_page"),
    )
    op.create_table(
        "factory_questions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("factory_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answered_by_id", sa.UUID(), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        _now(),
        sa.ForeignKeyConstraint(["factory_id"], ["factory_projects.page_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["answered_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_factory_questions_factory_id", "factory_questions", ["factory_id"])


def downgrade() -> None:
    op.drop_index("ix_factory_questions_factory_id", table_name="factory_questions")
    op.drop_table("factory_questions")
    op.drop_table("factory_sources")
    op.drop_table("factory_projects")
    # Enum values cannot be dropped: delete what uses them, then rebuild the type.
    op.execute("DELETE FROM pages WHERE type = 'factory'")
    op.execute("ALTER TYPE page_type RENAME TO page_type_old")
    op.execute(
        "CREATE TYPE page_type AS ENUM ('document', 'database', 'mindmap', 'folder', 'drawing')"
    )
    op.execute("ALTER TABLE pages ALTER COLUMN type TYPE page_type USING type::text::page_type")
    op.execute("DROP TYPE page_type_old")
