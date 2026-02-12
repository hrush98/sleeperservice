"""Add sport column to leagues.

Revision ID: 0011_add_league_sport
Revises: 0010_order_attempts
Create Date: 2026-02-11
"""

# pylint: disable=no-member

from alembic import op
import sqlalchemy as sa


revision = "0011_add_league_sport"
down_revision = "0010_order_attempts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("leagues", sa.Column("sport", sa.String(), nullable=True))
    op.execute("UPDATE leagues SET sport = 'lol' WHERE sport IS NULL")


def downgrade():
    op.drop_column("leagues", "sport")
