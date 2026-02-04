"""Add trade_events and unify positions table.

Revision ID: 0007_trade_events_positions
Revises: 0006_paper_positions
Create Date: 2026-02-02
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0007_trade_events_positions"
down_revision = "0006_paper_positions"
branch_labels = None
depends_on = None


def upgrade():
    op.rename_table("paper_positions", "positions")

    op.execute(
        "ALTER INDEX ix_paper_positions_mapping_opened RENAME TO ix_positions_mapping_opened"
    )
    op.execute("ALTER INDEX ix_paper_positions_opened RENAME TO ix_positions_opened")
    op.execute("ALTER INDEX ix_paper_positions_closed RENAME TO ix_positions_closed")

    op.drop_constraint(
        "fk_paper_positions_mapping_id", "positions", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_positions_mapping_id",
        "positions",
        "mappings",
        ["mapping_id"],
        ["id"],
    )

    op.add_column(
        "positions",
        sa.Column("mode", sa.String(), nullable=False, server_default="paper"),
    )
    op.add_column(
        "positions",
        sa.Column("pm_fixture_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("positions", sa.Column("venue", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_positions_pm_fixture_id",
        "positions",
        "fixtures",
        ["pm_fixture_id"],
        ["id"],
    )

    op.execute(
        """
        UPDATE positions
        SET pm_fixture_id = (raw_json->>'market_id')::uuid
        WHERE pm_fixture_id IS NULL
          AND raw_json ? 'market_id'
        """
    )

    op.create_index(
        "ix_positions_open_unique",
        "positions",
        ["mode", "pm_fixture_id", "side"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL"),
    )

    op.create_table(
        "trade_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("mapping_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pm_fixture_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("position_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("market_type", sa.String(), nullable=True),
        sa.Column("game_number", sa.Integer(), nullable=True),
        sa.Column("side", sa.String(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("edge_threshold", sa.Float(), nullable=True),
        sa.Column("spread_factor", sa.Float(), nullable=True),
        sa.Column("alpha_min", sa.Float(), nullable=True),
        sa.Column("alpha_spread_factor", sa.Float(), nullable=True),
        sa.Column("exit_epsilon", sa.Float(), nullable=True),
        sa.Column("p_ref_a", sa.Float(), nullable=True),
        sa.Column("p_ref_b", sa.Float(), nullable=True),
        sa.Column("bid_a", sa.Float(), nullable=True),
        sa.Column("ask_a", sa.Float(), nullable=True),
        sa.Column("mid_a", sa.Float(), nullable=True),
        sa.Column("bid_b", sa.Float(), nullable=True),
        sa.Column("ask_b", sa.Float(), nullable=True),
        sa.Column("mid_b", sa.Float(), nullable=True),
        sa.Column("best_edge", sa.Float(), nullable=True),
        sa.Column("best_side", sa.String(), nullable=True),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("limit_price", sa.Float(), nullable=True),
        sa.Column("avg_fill_price", sa.Float(), nullable=True),
        sa.Column("size_available", sa.Float(), nullable=True),
        sa.Column("net_edge", sa.Float(), nullable=True),
        sa.Column("exit_price", sa.Float(), nullable=True),
        sa.Column("pnl_percent", sa.Float(), nullable=True),
        sa.Column("external_order_id", sa.String(), nullable=True),
        sa.Column("external_fill_id", sa.String(), nullable=True),
        sa.Column("external_status", sa.String(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["mapping_id"],
            ["mappings.id"],
            name="fk_trade_events_mapping_id",
        ),
        sa.ForeignKeyConstraint(
            ["pm_fixture_id"],
            ["fixtures.id"],
            name="fk_trade_events_pm_fixture_id",
        ),
        sa.ForeignKeyConstraint(
            ["position_id"],
            ["positions.id"],
            name="fk_trade_events_position_id",
        ),
    )
    op.create_index(
        "ix_trade_events_mapping_ts",
        "trade_events",
        ["mapping_id", "ts"],
    )
    op.create_index(
        "ix_trade_events_position_ts",
        "trade_events",
        ["position_id", "ts"],
    )
    op.create_index(
        "ix_trade_events_run_ts",
        "trade_events",
        ["run_id", "ts"],
    )


def downgrade():
    op.drop_index("ix_trade_events_run_ts", table_name="trade_events")
    op.drop_index("ix_trade_events_position_ts", table_name="trade_events")
    op.drop_index("ix_trade_events_mapping_ts", table_name="trade_events")
    op.drop_table("trade_events")

    op.drop_index("ix_positions_open_unique", table_name="positions")
    op.drop_constraint("fk_positions_pm_fixture_id", "positions", type_="foreignkey")
    op.drop_column("positions", "venue")
    op.drop_column("positions", "pm_fixture_id")
    op.drop_column("positions", "mode")

    op.drop_constraint("fk_positions_mapping_id", "positions", type_="foreignkey")
    op.create_foreign_key(
        "fk_paper_positions_mapping_id",
        "positions",
        "mappings",
        ["mapping_id"],
        ["id"],
    )

    op.execute("ALTER INDEX ix_positions_mapping_opened RENAME TO ix_paper_positions_mapping_opened")
    op.execute("ALTER INDEX ix_positions_opened RENAME TO ix_paper_positions_opened")
    op.execute("ALTER INDEX ix_positions_closed RENAME TO ix_paper_positions_closed")

    op.rename_table("positions", "paper_positions")
