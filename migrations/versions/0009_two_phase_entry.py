"""Add two-phase entry fields to positions.

Revision ID: 0009_two_phase_entry
Revises: 0008_convergence_seconds
Create Date: 2026-02-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_two_phase_entry"
down_revision = "0008_convergence_seconds"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "positions",
        sa.Column("status", sa.String(), nullable=False, server_default="confirmed"),
    )
    op.add_column(
        "positions",
        sa.Column("external_order_id", sa.String(), nullable=True),
    )
    op.add_column(
        "positions",
        sa.Column("external_status", sa.String(), nullable=True),
    )

    op.execute(
        """
        UPDATE positions
        SET status = CASE
            WHEN closed_at IS NOT NULL THEN 'closed'
            ELSE 'confirmed'
        END
        """
    )


def downgrade():
    op.drop_column("positions", "external_status")
    op.drop_column("positions", "external_order_id")
    op.drop_column("positions", "status")
