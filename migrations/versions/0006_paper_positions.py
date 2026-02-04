"""Add paper_positions table.

Revision ID: 0006_paper_positions
Revises: 0005_add_fixture_hierarchy
Create Date: 2026-02-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_paper_positions"
down_revision = "0005_add_fixture_hierarchy"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "paper_positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("mapping_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("market_type", sa.String(), nullable=False),
        sa.Column("game_number", sa.Integer(), nullable=True),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("entry_p_ref", sa.Float(), nullable=True),
        sa.Column("entry_alpha", sa.Float(), nullable=True),
        sa.Column("entry_edge", sa.Float(), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("trigger_type", sa.String(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("exit_p_ref", sa.Float(), nullable=True),
        sa.Column("exit_reason", sa.String(), nullable=True),
        sa.Column("pnl_absolute", sa.Float(), nullable=True),
        sa.Column("pnl_percent", sa.Float(), nullable=True),
        sa.Column("hold_seconds", sa.Float(), nullable=True),
        sa.Column("edge_capture", sa.Float(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mapping_id"],
            ["mappings.id"],
            name="fk_paper_positions_mapping_id",
        ),
    )
    op.create_index(
        "ix_paper_positions_mapping_opened",
        "paper_positions",
        ["mapping_id", "opened_at"],
    )
    op.create_index(
        "ix_paper_positions_opened",
        "paper_positions",
        ["opened_at"],
    )
    op.create_index(
        "ix_paper_positions_closed",
        "paper_positions",
        ["closed_at"],
    )


def downgrade():
    op.drop_index("ix_paper_positions_closed", table_name="paper_positions")
    op.drop_index("ix_paper_positions_opened", table_name="paper_positions")
    op.drop_index("ix_paper_positions_mapping_opened", table_name="paper_positions")
    op.drop_table("paper_positions")
