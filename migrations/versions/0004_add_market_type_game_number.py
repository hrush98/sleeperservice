"""Add market_type and game_number to fixtures.

Revision ID: 0004_add_market_type_game_number
Revises: 0003_v2_simplified
Create Date: 2026-01-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_add_market_type_game_number"
down_revision = "0003_v2_simplified"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("fixtures", sa.Column("market_type", sa.String(), nullable=True))
    op.add_column("fixtures", sa.Column("game_number", sa.Integer(), nullable=True))
    op.create_index("ix_fixtures_market_type", "fixtures", ["market_type"])


def downgrade():
    op.drop_index("ix_fixtures_market_type", table_name="fixtures")
    op.drop_column("fixtures", "game_number")
    op.drop_column("fixtures", "market_type")

