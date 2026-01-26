"""v2 simplified schema - 6 tables for LoL Lead-Lag Arbitrage Bot

Revision ID: 0003_v2_simplified
Revises: 0002_external_ref_shadow
Create Date: 2026-01-24

This migration:
- Drops all old tables (markets, outcomes, settlement_specs, quote_snapshots, 
  derived_metrics, external_matches, external_odds_snapshots, 
  external_polymarket_mappings, disagreement_events, shadow_orders)
- Creates new simplified schema with 6 tables:
  - leagues
  - teams
  - fixtures
  - mappings
  - odds_snapshots
  - shadow_orders
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_v2_simplified"
down_revision = "0002_external_ref_shadow"
branch_labels = None
depends_on = None


def upgrade():
    # Drop old tables in reverse dependency order
    op.drop_table("shadow_orders")
    op.drop_table("disagreement_events")
    op.drop_table("external_polymarket_mappings")
    op.drop_table("external_odds_snapshots")
    op.drop_table("external_matches")
    op.drop_table("derived_metrics")
    op.drop_table("quote_snapshots")
    op.drop_table("settlement_specs")
    op.drop_table("outcomes")
    op.drop_table("markets")

    # Create new tables
    
    # 1. leagues
    op.create_table(
        "leagues",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("slug", sa.String(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_unique_constraint("uq_leagues_source_id", "leagues", ["source", "source_id"])
    op.create_index("ix_leagues_source", "leagues", ["source"])
    op.create_index("ix_leagues_name", "leagues", ["name"])

    # 2. teams
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("league_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leagues.id"), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("abbreviation", sa.String(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_unique_constraint("uq_teams_source_id", "teams", ["source", "source_id"])
    op.create_index("ix_teams_source", "teams", ["source"])
    op.create_index("ix_teams_name", "teams", ["name"])
    op.create_index("ix_teams_league_id", "teams", ["league_id"])

    # 3. fixtures
    op.create_table(
        "fixtures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("league_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("leagues.id"), nullable=True),
        sa.Column("team_a_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("team_b_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("team_a_name", sa.String(), nullable=True),
        sa.Column("team_b_name", sa.String(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="upcoming"),
        sa.Column("has_odds", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_unique_constraint("uq_fixtures_source_id", "fixtures", ["source", "source_id"])
    op.create_index("ix_fixtures_source", "fixtures", ["source"])
    op.create_index("ix_fixtures_status", "fixtures", ["status"])
    op.create_index("ix_fixtures_start_time", "fixtures", ["start_time"])
    op.create_index("ix_fixtures_league_id", "fixtures", ["league_id"])

    # 4. mappings
    op.create_table(
        "mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("oddspapi_fixture_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fixtures.id"), nullable=False),
        sa.Column("polymarket_fixture_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fixtures.id"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("method", sa.String(), nullable=False, server_default="'auto'"),
        sa.Column("match_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_unique_constraint("uq_mappings_fixtures", "mappings", ["oddspapi_fixture_id", "polymarket_fixture_id"])
    op.create_index("ix_mappings_oddspapi_fixture", "mappings", ["oddspapi_fixture_id"])
    op.create_index("ix_mappings_polymarket_fixture", "mappings", ["polymarket_fixture_id"])
    op.create_index("ix_mappings_confidence", "mappings", ["confidence"])

    # 5. odds_snapshots
    op.create_table(
        "odds_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("fixture_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("fixtures.id"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("team_a_odds", sa.Float(), nullable=True),
        sa.Column("team_b_odds", sa.Float(), nullable=True),
        sa.Column("team_a_implied_prob", sa.Float(), nullable=True),
        sa.Column("team_b_implied_prob", sa.Float(), nullable=True),
        sa.Column("best_bid", sa.Float(), nullable=True),
        sa.Column("best_ask", sa.Float(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index("ix_odds_snapshots_fixture_ts", "odds_snapshots", ["fixture_id", "ts"])
    op.create_index("ix_odds_snapshots_source", "odds_snapshots", ["source"])

    # 6. shadow_orders
    op.create_table(
        "shadow_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("mapping_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("mappings.id"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("pinnacle_prob", sa.Float(), nullable=False),
        sa.Column("polymarket_price", sa.Float(), nullable=False),
        sa.Column("gap", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index("ix_shadow_orders_mapping_ts", "shadow_orders", ["mapping_id", "ts"])
    op.create_index("ix_shadow_orders_ts", "shadow_orders", ["ts"])


def downgrade():
    # Drop new tables
    op.drop_index("ix_shadow_orders_ts", table_name="shadow_orders")
    op.drop_index("ix_shadow_orders_mapping_ts", table_name="shadow_orders")
    op.drop_table("shadow_orders")

    op.drop_index("ix_odds_snapshots_source", table_name="odds_snapshots")
    op.drop_index("ix_odds_snapshots_fixture_ts", table_name="odds_snapshots")
    op.drop_table("odds_snapshots")

    op.drop_index("ix_mappings_confidence", table_name="mappings")
    op.drop_index("ix_mappings_polymarket_fixture", table_name="mappings")
    op.drop_index("ix_mappings_oddspapi_fixture", table_name="mappings")
    op.drop_constraint("uq_mappings_fixtures", "mappings", type_="unique")
    op.drop_table("mappings")

    op.drop_index("ix_fixtures_league_id", table_name="fixtures")
    op.drop_index("ix_fixtures_start_time", table_name="fixtures")
    op.drop_index("ix_fixtures_status", table_name="fixtures")
    op.drop_index("ix_fixtures_source", table_name="fixtures")
    op.drop_constraint("uq_fixtures_source_id", "fixtures", type_="unique")
    op.drop_table("fixtures")

    op.drop_index("ix_teams_league_id", table_name="teams")
    op.drop_index("ix_teams_name", table_name="teams")
    op.drop_index("ix_teams_source", table_name="teams")
    op.drop_constraint("uq_teams_source_id", "teams", type_="unique")
    op.drop_table("teams")

    op.drop_index("ix_leagues_name", table_name="leagues")
    op.drop_index("ix_leagues_source", table_name="leagues")
    op.drop_constraint("uq_leagues_source_id", "leagues", type_="unique")
    op.drop_table("leagues")

    # Note: Downgrade would need to recreate old tables, but that's complex.
    # For MVP purposes, we'll just note this would require manual intervention.
    # In practice, we'd recreate the old schema here.

