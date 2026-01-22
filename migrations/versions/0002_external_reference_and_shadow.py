"""external reference + mapping + shadow execution tables

Revision ID: 0002_external_reference_and_shadow
Revises: 0001_initial
Create Date: 2026-01-22

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0002_external_reference_and_shadow"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "external_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_match_id", sa.String(), nullable=False),
        sa.Column("league", sa.String(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("team_a", sa.String(), nullable=False),
        sa.Column("team_b", sa.String(), nullable=False),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_external_matches_source_match", "external_matches", ["source", "external_match_id"]
    )
    op.create_index(
        "ix_external_matches_source_match",
        "external_matches",
        ["source", "external_match_id"],
    )
    op.create_index("ix_external_matches_start_time", "external_matches", ["start_time"])
    op.create_index("ix_external_matches_updated_at", "external_matches", ["updated_at"])

    op.create_table(
        "external_odds_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "external_match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("external_matches.id"),
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market_type", sa.String(), nullable=False),
        sa.Column("selection", sa.String(), nullable=False),
        sa.Column("odds_decimal", sa.Float(), nullable=True),
        sa.Column("odds_american", sa.Integer(), nullable=True),
        sa.Column("implied_prob", sa.Float(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index(
        "ix_external_odds_snapshots_match_ts",
        "external_odds_snapshots",
        ["external_match_id", "ts"],
    )
    op.create_unique_constraint(
        "uq_external_odds_snapshots_natural",
        "external_odds_snapshots",
        ["external_match_id", "market_type", "selection", "ts"],
    )

    op.create_table(
        "external_polymarket_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column(
            "external_match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("external_matches.id"),
            nullable=False,
        ),
        sa.Column(
            "polymarket_market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id"),
            nullable=False,
        ),
        sa.Column(
            "polymarket_outcome_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outcomes.id"),
            nullable=True,
        ),
        sa.Column("mapping_confidence", sa.Float(), nullable=True),
        sa.Column("mapping_method", sa.String(), nullable=False),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_external_polymarket_mapping",
        "external_polymarket_mappings",
        ["source", "external_match_id", "polymarket_market_id", "polymarket_outcome_id"],
    )
    op.create_index(
        "ix_external_polymarket_mappings_external",
        "external_polymarket_mappings",
        ["external_match_id", "updated_at"],
    )
    op.create_index(
        "ix_external_polymarket_mappings_market",
        "external_polymarket_mappings",
        ["polymarket_market_id", "updated_at"],
    )

    op.create_table(
        "disagreement_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "external_match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("external_matches.id"),
            nullable=False,
        ),
        sa.Column(
            "polymarket_market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id"),
            nullable=False,
        ),
        sa.Column(
            "polymarket_outcome_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outcomes.id"),
            nullable=False,
        ),
        sa.Column("ref_implied_prob", sa.Float(), nullable=False),
        sa.Column("poly_mid", sa.Float(), nullable=True),
        sa.Column("poly_best_bid", sa.Float(), nullable=True),
        sa.Column("poly_best_ask", sa.Float(), nullable=True),
        sa.Column("gap", sa.Float(), nullable=False),
        sa.Column("edge", sa.Float(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index(
        "ix_disagreement_events_market_ts", "disagreement_events", ["polymarket_market_id", "ts"]
    )
    op.create_index(
        "ix_disagreement_events_external_ts", "disagreement_events", ["external_match_id", "ts"]
    )

    op.create_table(
        "shadow_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "external_match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("external_matches.id"),
            nullable=False,
        ),
        sa.Column(
            "polymarket_market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id"),
            nullable=False,
        ),
        sa.Column(
            "polymarket_outcome_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outcomes.id"),
            nullable=False,
        ),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("size", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index("ix_shadow_orders_market_ts", "shadow_orders", ["polymarket_market_id", "ts"])
    op.create_index(
        "ix_shadow_orders_external_ts", "shadow_orders", ["external_match_id", "ts"]
    )


def downgrade():
    op.drop_index("ix_shadow_orders_external_ts", table_name="shadow_orders")
    op.drop_index("ix_shadow_orders_market_ts", table_name="shadow_orders")
    op.drop_table("shadow_orders")

    op.drop_index("ix_disagreement_events_external_ts", table_name="disagreement_events")
    op.drop_index("ix_disagreement_events_market_ts", table_name="disagreement_events")
    op.drop_table("disagreement_events")

    op.drop_index(
        "ix_external_polymarket_mappings_market", table_name="external_polymarket_mappings"
    )
    op.drop_index(
        "ix_external_polymarket_mappings_external", table_name="external_polymarket_mappings"
    )
    op.drop_constraint(
        "uq_external_polymarket_mapping", "external_polymarket_mappings", type_="unique"
    )
    op.drop_table("external_polymarket_mappings")

    op.drop_constraint(
        "uq_external_odds_snapshots_natural", "external_odds_snapshots", type_="unique"
    )
    op.drop_index("ix_external_odds_snapshots_match_ts", table_name="external_odds_snapshots")
    op.drop_table("external_odds_snapshots")

    op.drop_index("ix_external_matches_updated_at", table_name="external_matches")
    op.drop_index("ix_external_matches_start_time", table_name="external_matches")
    op.drop_index("ix_external_matches_source_match", table_name="external_matches")
    op.drop_constraint(
        "uq_external_matches_source_match", "external_matches", type_="unique"
    )
    op.drop_table("external_matches")


