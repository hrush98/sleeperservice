"""initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-01-18

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "markets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("platform_market_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_markets_platform_market", "markets", ["platform", "platform_market_id"])
    op.create_unique_constraint("uq_markets_platform_market", "markets", ["platform", "platform_market_id"])
    op.create_index("ix_markets_status", "markets", ["status"])
    op.create_index("ix_markets_updated_at", "markets", ["updated_at"])

    op.create_table(
        "outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("market_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("outcome_name", sa.String(), nullable=False),
        sa.Column("platform_outcome_id", sa.String(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint("uq_outcomes_market_name", "outcomes", ["market_id", "outcome_name"])
    op.create_index("ix_outcomes_market_id", "outcomes", ["market_id"])
    op.create_index(
        "uq_outcomes_market_platform_outcome",
        "outcomes",
        ["market_id", "platform_outcome_id"],
        unique=True,
        postgresql_where=sa.text("platform_outcome_id IS NOT NULL"),
    )

    op.create_table(
        "settlement_specs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("market_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("resolution_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criteria_text", sa.Text(), nullable=True),
        sa.Column("spec_version_hash", sa.String(), nullable=False),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_unique_constraint(
        "uq_settlement_spec_version", "settlement_specs", ["market_id", "spec_version_hash"]
    )
    op.create_index(
        "ix_settlement_specs_market_created",
        "settlement_specs",
        ["market_id", "created_at"],
    )

    op.create_table(
        "quote_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("market_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column(
            "outcome_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("outcomes.id"),
            nullable=True,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("volume_24h", sa.Float(), nullable=True),
        sa.Column("liquidity", sa.Float(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index("ix_quote_snapshots_market_ts", "quote_snapshots", ["market_id", "ts"])
    op.create_index("ix_quote_snapshots_outcome_ts", "quote_snapshots", ["outcome_id", "ts"])
    op.create_index(
        "uq_quote_snapshots_market_outcome_ts",
        "quote_snapshots",
        ["market_id", "outcome_id", "ts"],
        unique=True,
        postgresql_where=sa.text("outcome_id IS NOT NULL"),
    )
    op.create_index(
        "uq_quote_snapshots_market_ts_null_outcome",
        "quote_snapshots",
        ["market_id", "ts"],
        unique=True,
        postgresql_where=sa.text("outcome_id IS NULL"),
    )

    op.create_table(
        "derived_metrics",
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("move_24h", sa.Float(), nullable=True),
        sa.Column("interesting_score", sa.Float(), nullable=True),
        sa.Column("components", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade():
    op.drop_table("derived_metrics")
    op.drop_index("uq_quote_snapshots_market_ts_null_outcome", table_name="quote_snapshots")
    op.drop_index("uq_quote_snapshots_market_outcome_ts", table_name="quote_snapshots")
    op.drop_index("ix_quote_snapshots_outcome_ts", table_name="quote_snapshots")
    op.drop_index("ix_quote_snapshots_market_ts", table_name="quote_snapshots")
    op.drop_table("quote_snapshots")
    op.drop_index("ix_settlement_specs_market_created", table_name="settlement_specs")
    op.drop_constraint("uq_settlement_spec_version", "settlement_specs", type_="unique")
    op.drop_table("settlement_specs")
    op.drop_index("uq_outcomes_market_platform_outcome", table_name="outcomes")
    op.drop_index("ix_outcomes_market_id", table_name="outcomes")
    op.drop_constraint("uq_outcomes_market_name", "outcomes", type_="unique")
    op.drop_table("outcomes")
    op.drop_index("ix_markets_updated_at", table_name="markets")
    op.drop_index("ix_markets_status", table_name="markets")
    op.drop_constraint("uq_markets_platform_market", "markets", type_="unique")
    op.drop_index("ix_markets_platform_market", table_name="markets")
    op.drop_table("markets")
