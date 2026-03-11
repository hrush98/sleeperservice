"""Add complement arb table and strategy columns.

Revision ID: 0014_complement_arb_strategy
Revises: 0013_add_fixture_line_value
Create Date: 2026-02-18
"""

# pylint: disable=no-member,not-callable

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0014_complement_arb_strategy"
down_revision = "0013_add_fixture_line_value"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "positions",
        sa.Column("strategy", sa.String(), nullable=False, server_default="lead_lag"),
    )
    op.add_column(
        "order_attempts",
        sa.Column("strategy", sa.String(), nullable=False, server_default="lead_lag"),
    )
    op.add_column(
        "trade_events",
        sa.Column("strategy", sa.String(), nullable=False, server_default="lead_lag"),
    )

    op.create_index(
        "ix_positions_strategy_opened",
        "positions",
        ["strategy", "opened_at"],
    )
    op.create_index(
        "ix_order_attempts_strategy_submitted",
        "order_attempts",
        ["strategy", "submitted_at"],
    )
    op.create_index(
        "ix_trade_events_strategy_ts",
        "trade_events",
        ["strategy", "ts"],
    )

    op.create_table(
        "complement_arbs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "mapping_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mappings.id"),
            nullable=False,
        ),
        sa.Column(
            "pm_fixture_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fixtures.id"),
            nullable=True,
        ),
        sa.Column("market_type", sa.String(), nullable=False),
        sa.Column("game_number", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="IDLE"),
        sa.Column(
            "leg_a_position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("positions.id"),
            nullable=True,
        ),
        sa.Column(
            "leg_b_position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("positions.id"),
            nullable=True,
        ),
        sa.Column("vwap_a", sa.Float(), nullable=True),
        sa.Column("vwap_b", sa.Float(), nullable=True),
        sa.Column("target_size", sa.Float(), nullable=True),
        sa.Column("locked_edge", sa.Float(), nullable=True),
        sa.Column("actual_cost_a", sa.Float(), nullable=True),
        sa.Column("actual_cost_b", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_pnl", sa.Float(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_complement_arbs_mapping_created",
        "complement_arbs",
        ["mapping_id", "created_at"],
    )
    op.create_index(
        "ix_complement_arbs_state_created",
        "complement_arbs",
        ["state", "created_at"],
    )
    op.create_index(
        "ix_complement_arbs_run_created",
        "complement_arbs",
        ["run_id", "created_at"],
    )

    op.alter_column("positions", "strategy", server_default=None)
    op.alter_column("order_attempts", "strategy", server_default=None)
    op.alter_column("trade_events", "strategy", server_default=None)
    op.alter_column("complement_arbs", "state", server_default=None)


def downgrade():
    op.drop_index("ix_complement_arbs_run_created", table_name="complement_arbs")
    op.drop_index("ix_complement_arbs_state_created", table_name="complement_arbs")
    op.drop_index("ix_complement_arbs_mapping_created", table_name="complement_arbs")
    op.drop_table("complement_arbs")

    op.drop_index("ix_trade_events_strategy_ts", table_name="trade_events")
    op.drop_index("ix_order_attempts_strategy_submitted", table_name="order_attempts")
    op.drop_index("ix_positions_strategy_opened", table_name="positions")

    op.drop_column("trade_events", "strategy")
    op.drop_column("order_attempts", "strategy")
    op.drop_column("positions", "strategy")
