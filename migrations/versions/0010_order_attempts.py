"""Add order_attempts table for execution tracking.

Revision ID: 0010_order_attempts
Revises: 0009_two_phase_entry
Create Date: 2026-02-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0010_order_attempts"
down_revision = "0009_two_phase_entry"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "order_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("positions.id"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("token_id", sa.String(), nullable=False),
        sa.Column("attempt_seq", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("requested_size", sa.Float(), nullable=True),
        sa.Column("external_order_id", sa.String(), nullable=True),
        sa.Column("external_status", sa.String(), nullable=True),
        sa.Column("matched_size", sa.Float(), nullable=True),
        sa.Column("not_found_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_state", sa.String(), nullable=True),
        sa.Column("final_reason", sa.Text(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=False),
    )

    op.create_index(
        "ix_order_attempts_position_phase_seq",
        "order_attempts",
        ["position_id", "phase", "attempt_seq"],
        unique=True,
    )
    op.create_index(
        "ix_order_attempts_pending",
        "order_attempts",
        ["finalized_at"],
        postgresql_where=sa.text("finalized_at IS NULL"),
    )
    op.create_index(
        "ix_order_attempts_external_order_id",
        "order_attempts",
        ["external_order_id"],
    )


def downgrade():
    op.drop_index("ix_order_attempts_external_order_id", table_name="order_attempts")
    op.drop_index("ix_order_attempts_pending", table_name="order_attempts")
    op.drop_index("ix_order_attempts_position_phase_seq", table_name="order_attempts")
    op.drop_table("order_attempts")
