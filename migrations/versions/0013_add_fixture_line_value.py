"""Add fixture line value for totals markets.

Revision ID: 0013_add_fixture_line_value
Revises: 0012_gold_edge_tables
Create Date: 2026-02-13
"""

# pylint: disable=no-member,not-callable

from alembic import op
import sqlalchemy as sa


revision = "0013_add_fixture_line_value"
down_revision = "0012_gold_edge_tables"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("fixtures", sa.Column("line_value", sa.Float(), nullable=True))
    op.create_index(
        "ix_fixtures_market_type_line_value",
        "fixtures",
        ["market_type", "line_value"],
    )


def downgrade():
    op.drop_index("ix_fixtures_market_type_line_value", table_name="fixtures")
    op.drop_column("fixtures", "line_value")
