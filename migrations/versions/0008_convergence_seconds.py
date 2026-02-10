"""Add convergence seconds to positions and trade events.

Revision ID: 0008_convergence_seconds
Revises: 0007_trade_events_positions
Create Date: 2026-02-02
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0008_convergence_seconds"
down_revision = "0007_trade_events_positions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "positions",
        sa.Column("convergence_seconds", sa.Float(), nullable=True),
    )
    op.add_column(
        "trade_events",
        sa.Column("convergence_seconds", sa.Float(), nullable=True),
    )


def downgrade():
    op.drop_column("trade_events", "convergence_seconds")
    op.drop_column("positions", "convergence_seconds")
