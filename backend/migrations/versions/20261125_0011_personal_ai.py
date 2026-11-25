"""Personal chat provider profiles.

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("ai_provider", sa.String(20), nullable=True))
    op.create_table(
        "user_ai_profiles",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("provider", sa.String(20), primary_key=True),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("encrypted_key", sa.LargeBinary(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_ai_profiles")
    op.drop_column("users", "ai_provider")
