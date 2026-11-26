"""Database engine v2: formula, date range and file properties.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TYPES = ("formula", "date_range", "file")


def upgrade() -> None:
    for value in NEW_TYPES:
        op.execute(f"ALTER TYPE property_type ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres cannot drop enum values; turn the new properties into text first.
    op.execute("DELETE FROM database_properties WHERE type::text IN ('formula')")
    op.execute(
        "UPDATE database_properties SET type = 'text', config = '{}'::jsonb "
        "WHERE type::text IN ('date_range', 'file')"
    )
    op.execute("ALTER TYPE property_type RENAME TO property_type_v2")
    op.execute(
        "CREATE TYPE property_type AS ENUM ('title', 'text', 'number', 'select', "
        "'multi_select', 'date', 'checkbox', 'url', 'person', 'relation', 'rollup')"
    )
    op.execute(
        "ALTER TABLE database_properties ALTER COLUMN type TYPE property_type "
        "USING type::text::property_type"
    )
    op.execute("DROP TYPE property_type_v2")
