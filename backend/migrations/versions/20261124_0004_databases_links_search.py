"""Databases (properties, rows, relations, views), page links and full-text search.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "database_properties",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("database_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "title",
                "text",
                "number",
                "select",
                "multi_select",
                "date",
                "checkbox",
                "url",
                "person",
                "relation",
                "rollup",
                name="property_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["database_id", "workspace_id"],
            ["pages.id", "pages.workspace_id"],
            name="fk_database_properties_database_same_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_database_properties")),
    )
    op.create_index(
        "ix_database_properties_database_id", "database_properties", ["database_id"], unique=False
    )
    op.create_table(
        "database_rows",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("database_id", sa.UUID(), nullable=False),
        sa.Column(
            "values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("position", sa.Double(), server_default=sa.text("0"), nullable=False),
        sa.Column("search_text", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, search_text)", persisted=True),
            nullable=True,
        ),
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column("updated_by_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_database_rows_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["database_id", "workspace_id"],
            ["pages.id", "pages.workspace_id"],
            name="fk_database_rows_database_same_workspace",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_database_rows_updated_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_database_rows")),
    )
    op.create_index(
        "ix_database_rows_database_id_position",
        "database_rows",
        ["database_id", "position"],
        unique=False,
    )
    op.create_index(
        "ix_database_rows_search_vector",
        "database_rows",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "database_views",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("database_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("type", sa.Enum("table", "board", name="view_type"), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["database_id", "workspace_id"],
            ["pages.id", "pages.workspace_id"],
            name="fk_database_views_database_same_workspace",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_database_views")),
    )
    op.create_index(
        "ix_database_views_database_id", "database_views", ["database_id"], unique=False
    )
    op.create_table(
        "page_links",
        sa.Column("source_page_id", sa.UUID(), nullable=False),
        sa.Column("target_page_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_page_id"],
            ["pages.id"],
            name=op.f("fk_page_links_source_page_id_pages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_page_id"],
            ["pages.id"],
            name=op.f("fk_page_links_target_page_id_pages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_page_id", "target_page_id", name=op.f("pk_page_links")),
    )
    op.create_index("ix_page_links_target_page_id", "page_links", ["target_page_id"], unique=False)
    op.create_table(
        "database_relations",
        sa.Column("property_id", sa.UUID(), nullable=False),
        sa.Column("from_row_id", sa.UUID(), nullable=False),
        sa.Column("to_row_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(
            ["from_row_id"],
            ["database_rows.id"],
            name=op.f("fk_database_relations_from_row_id_database_rows"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["database_properties.id"],
            name=op.f("fk_database_relations_property_id_database_properties"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_row_id"],
            ["database_rows.id"],
            name=op.f("fk_database_relations_to_row_id_database_rows"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "property_id", "from_row_id", "to_row_id", name=op.f("pk_database_relations")
        ),
    )
    op.create_index(
        "ix_database_relations_to_row_id", "database_relations", ["to_row_id"], unique=False
    )
    op.add_column(
        "pages", sa.Column("content_text", sa.Text(), server_default=sa.text("''"), nullable=False)
    )
    op.add_column(
        "pages",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('simple'::regconfig, title), 'A') || "
                "setweight(to_tsvector('simple'::regconfig, content_text), 'B')",
                persisted=True,
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_pages_search_vector", "pages", ["search_vector"], unique=False, postgresql_using="gin"
    )
    op.create_index(
        "ix_pages_title_trgm",
        "pages",
        ["title"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pages_title_trgm",
        table_name="pages",
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )
    op.drop_index("ix_pages_search_vector", table_name="pages", postgresql_using="gin")
    op.drop_column("pages", "search_vector")
    op.drop_column("pages", "content_text")
    op.drop_index("ix_database_relations_to_row_id", table_name="database_relations")
    op.drop_table("database_relations")
    op.drop_index("ix_page_links_target_page_id", table_name="page_links")
    op.drop_table("page_links")
    op.drop_index("ix_database_views_database_id", table_name="database_views")
    op.drop_table("database_views")
    op.drop_index(
        "ix_database_rows_search_vector", table_name="database_rows", postgresql_using="gin"
    )
    op.drop_index("ix_database_rows_database_id_position", table_name="database_rows")
    op.drop_table("database_rows")
    op.drop_index("ix_database_properties_database_id", table_name="database_properties")
    op.drop_table("database_properties")
    sa.Enum(name="view_type").drop(op.get_bind())
    sa.Enum(name="property_type").drop(op.get_bind())
