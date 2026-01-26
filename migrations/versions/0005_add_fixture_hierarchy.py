"""Add series_type and parent_fixture_id to fixtures.

Revision ID: 0005_add_fixture_hierarchy
Revises: 0004_add_market_type_game_number
Create Date: 2026-01-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005_add_fixture_hierarchy"
down_revision = "0004_add_market_type_game_number"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("fixtures", sa.Column("series_type", sa.String(), nullable=True))
    op.add_column(
        "fixtures",
        sa.Column("parent_fixture_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_fixtures_parent_fixture_id",
        "fixtures",
        "fixtures",
        ["parent_fixture_id"],
        ["id"],
    )
    op.create_index("ix_fixtures_parent_fixture_id", "fixtures", ["parent_fixture_id"])


def downgrade():
    op.drop_index("ix_fixtures_parent_fixture_id", table_name="fixtures")
    op.drop_constraint("fk_fixtures_parent_fixture_id", "fixtures", type_="foreignkey")
    op.drop_column("fixtures", "parent_fixture_id")
    op.drop_column("fixtures", "series_type")

