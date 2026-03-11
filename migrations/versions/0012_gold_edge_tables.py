"""Add gold-edge strategy tables.

Revision ID: 0012_gold_edge_tables
Revises: 0011_add_league_sport
Create Date: 2026-02-12
"""

# pylint: disable=no-member,not-callable

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0012_gold_edge_tables"
down_revision = "0011_add_league_sport"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "game_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_match_id", sa.String(), nullable=False),
        sa.Column("source_league", sa.String(), nullable=True),
        sa.Column("source_date", sa.String(), nullable=True),
        sa.Column("source_time", sa.String(), nullable=True),
        sa.Column("game_no", sa.Integer(), nullable=True),
        sa.Column("team_a_name", sa.String(), nullable=False),
        sa.Column("team_b_name", sa.String(), nullable=False),
        sa.Column("team_a_score", sa.Float(), nullable=True),
        sa.Column("team_b_score", sa.Float(), nullable=True),
        sa.Column("game_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("gold_a", sa.Float(), nullable=True),
        sa.Column("gold_b", sa.Float(), nullable=True),
        sa.Column("gold_diff", sa.Float(), nullable=True),
        sa.Column("kills_a", sa.Float(), nullable=True),
        sa.Column("kills_b", sa.Float(), nullable=True),
        sa.Column("towers_a", sa.Float(), nullable=True),
        sa.Column("towers_b", sa.Float(), nullable=True),
        sa.Column("dragons_a", sa.Float(), nullable=True),
        sa.Column("dragons_b", sa.Float(), nullable=True),
        sa.Column("barons_a", sa.Float(), nullable=True),
        sa.Column("barons_b", sa.Float(), nullable=True),
        sa.Column("inhibitors_a", sa.Float(), nullable=True),
        sa.Column("inhibitors_b", sa.Float(), nullable=True),
        sa.Column(
            "pm_fixture_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fixtures.id"),
            nullable=True,
        ),
        sa.Column("pm_token_id_a", sa.String(), nullable=True),
        sa.Column("pm_token_id_b", sa.String(), nullable=True),
        sa.Column("pm_bid_a", sa.Float(), nullable=True),
        sa.Column("pm_ask_a", sa.Float(), nullable=True),
        sa.Column("pm_bid_b", sa.Float(), nullable=True),
        sa.Column("pm_ask_b", sa.Float(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_game_snapshots_source_match_ts",
        "game_snapshots",
        ["source_match_id", "ts"],
    )
    op.create_index(
        "ix_game_snapshots_game_no_ts",
        "game_snapshots",
        ["game_no", "ts"],
    )
    op.create_index(
        "ix_game_snapshots_pm_fixture_ts",
        "game_snapshots",
        ["pm_fixture_id", "ts"],
    )

    op.create_table(
        "game_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_match_id", sa.String(), nullable=False),
        sa.Column("game_no", sa.Integer(), nullable=False),
        sa.Column("source_date", sa.String(), nullable=True),
        sa.Column("source_league", sa.String(), nullable=True),
        sa.Column("team_a_name", sa.String(), nullable=False),
        sa.Column("team_b_name", sa.String(), nullable=False),
        sa.Column("winner_side", sa.String(), nullable=True),
        sa.Column("final_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("final_gold_a", sa.Float(), nullable=True),
        sa.Column("final_gold_b", sa.Float(), nullable=True),
        sa.Column("final_kills_a", sa.Float(), nullable=True),
        sa.Column("final_kills_b", sa.Float(), nullable=True),
        sa.Column("final_towers_a", sa.Float(), nullable=True),
        sa.Column("final_towers_b", sa.Float(), nullable=True),
        sa.Column("final_dragons_a", sa.Float(), nullable=True),
        sa.Column("final_dragons_b", sa.Float(), nullable=True),
        sa.Column("final_barons_a", sa.Float(), nullable=True),
        sa.Column("final_barons_b", sa.Float(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("source_match_id", "game_no", name="uq_game_results_match_game"),
    )
    op.create_index("ix_game_results_source_date", "game_results", ["source_date"])

    op.create_table(
        "gold_edge_trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="open"),
        sa.Column("source_match_id", sa.String(), nullable=False),
        sa.Column("game_no", sa.Integer(), nullable=True),
        sa.Column("team_a_name", sa.String(), nullable=False),
        sa.Column("team_b_name", sa.String(), nullable=False),
        sa.Column("picked_side", sa.String(), nullable=False),
        sa.Column(
            "pm_fixture_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fixtures.id"),
            nullable=True,
        ),
        sa.Column("token_id", sa.String(), nullable=True),
        sa.Column("entry_price", sa.Float(), nullable=False),
        sa.Column("model_prob", sa.Float(), nullable=False),
        sa.Column("edge_at_entry", sa.Float(), nullable=False),
        sa.Column("stake_usd", sa.Float(), nullable=False),
        sa.Column("shares", sa.Float(), nullable=True),
        sa.Column("order_id", sa.String(), nullable=True),
        sa.Column("order_status", sa.String(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("winner_side", sa.String(), nullable=True),
        sa.Column("pnl_usd", sa.Float(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_gold_edge_trades_open", "gold_edge_trades", ["status"])
    op.create_index("ix_gold_edge_trades_ts", "gold_edge_trades", ["ts"])
    op.create_index("ix_gold_edge_trades_source_match", "gold_edge_trades", ["source_match_id"])


def downgrade():
    op.drop_index("ix_gold_edge_trades_source_match", table_name="gold_edge_trades")
    op.drop_index("ix_gold_edge_trades_ts", table_name="gold_edge_trades")
    op.drop_index("ix_gold_edge_trades_open", table_name="gold_edge_trades")
    op.drop_table("gold_edge_trades")

    op.drop_index("ix_game_results_source_date", table_name="game_results")
    op.drop_table("game_results")

    op.drop_index("ix_game_snapshots_pm_fixture_ts", table_name="game_snapshots")
    op.drop_index("ix_game_snapshots_game_no_ts", table_name="game_snapshots")
    op.drop_index("ix_game_snapshots_source_match_ts", table_name="game_snapshots")
    op.drop_table("game_snapshots")
