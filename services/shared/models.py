"""
LoL Lead-Lag Arbitrage Bot - Database Models (v2 simplified schema)

6 tables:
- leagues: Tournament/league metadata from both sources
- teams: Team/participant cache from both sources
- fixtures: Upcoming/live matches from both sources
- mappings: OddsPapi fixture ↔ Polymarket fixture links
- odds_snapshots: Live price time series (append-only)
- shadow_orders: Paper trade decisions (append-only)
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db import Base


class League(Base):
    """League/tournament metadata from OddsPapi or Polymarket."""

    __tablename__ = "leagues"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(String, nullable=False)  # "oddspapi" | "polymarket"
    source_id: Mapped[str] = mapped_column(String, nullable=False)  # tournament_id or series_id
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    teams: Mapped[list[Team]] = relationship("Team", back_populates="league")
    fixtures: Mapped[list[Fixture]] = relationship("Fixture", back_populates="league")

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_leagues_source_id"),
        Index("ix_leagues_source", "source"),
        Index("ix_leagues_name", "name"),
    )


class Team(Base):
    """Team/participant cache from OddsPapi or Polymarket."""

    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(String, nullable=False)  # "oddspapi" | "polymarket"
    source_id: Mapped[str] = mapped_column(String, nullable=False)  # participant_id or team_id
    league_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leagues.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    abbreviation: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    league: Mapped[League | None] = relationship("League", back_populates="teams")

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_teams_source_id"),
        Index("ix_teams_source", "source"),
        Index("ix_teams_name", "name"),
        Index("ix_teams_league_id", "league_id"),
    )


class Fixture(Base):
    """Upcoming/live match from OddsPapi or Polymarket."""

    __tablename__ = "fixtures"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(String, nullable=False)  # "oddspapi" | "polymarket"
    source_id: Mapped[str] = mapped_column(String, nullable=False)  # fixture_id or event_id/market_id
    league_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leagues.id"), nullable=True
    )
    team_a_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True
    )
    team_b_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id"), nullable=True
    )
    team_a_name: Mapped[str | None] = mapped_column(String, nullable=True)  # Denormalized for convenience
    team_b_name: Mapped[str | None] = mapped_column(String, nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="upcoming")  # upcoming|live|finished
    has_odds: Mapped[bool] = mapped_column(default=False)
    market_type: Mapped[str | None] = mapped_column(String, nullable=True)  # match_winner|game_winner
    game_number: Mapped[int | None] = mapped_column(nullable=True)  # 1/2/3 for game_winner
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    league: Mapped[League | None] = relationship("League", back_populates="fixtures")
    team_a: Mapped[Team | None] = relationship("Team", foreign_keys=[team_a_id])
    team_b: Mapped[Team | None] = relationship("Team", foreign_keys=[team_b_id])
    odds_snapshots: Mapped[list[OddsSnapshot]] = relationship("OddsSnapshot", back_populates="fixture")

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_fixtures_source_id"),
        Index("ix_fixtures_source", "source"),
        Index("ix_fixtures_status", "status"),
        Index("ix_fixtures_start_time", "start_time"),
        Index("ix_fixtures_league_id", "league_id"),
        Index("ix_fixtures_market_type", "market_type"),
    )


class Mapping(Base):
    """Link between OddsPapi fixture and Polymarket fixture."""

    __tablename__ = "mappings"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    oddspapi_fixture_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fixtures.id"), nullable=False
    )
    polymarket_fixture_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fixtures.id"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    method: Mapped[str] = mapped_column(String, nullable=False, default="auto")  # auto|manual
    match_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # What matched: league, teams, date
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    oddspapi_fixture: Mapped[Fixture] = relationship("Fixture", foreign_keys=[oddspapi_fixture_id])
    polymarket_fixture: Mapped[Fixture] = relationship("Fixture", foreign_keys=[polymarket_fixture_id])
    shadow_orders: Mapped[list[ShadowOrder]] = relationship("ShadowOrder", back_populates="mapping")

    __table_args__ = (
        UniqueConstraint("oddspapi_fixture_id", "polymarket_fixture_id", name="uq_mappings_fixtures"),
        Index("ix_mappings_oddspapi_fixture", "oddspapi_fixture_id"),
        Index("ix_mappings_polymarket_fixture", "polymarket_fixture_id"),
        Index("ix_mappings_confidence", "confidence"),
    )


class OddsSnapshot(Base):
    """Live price snapshot from OddsPapi or Polymarket CLOB (append-only)."""

    __tablename__ = "odds_snapshots"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    fixture_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fixtures.id"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)  # "oddspapi" | "polymarket_clob"
    # OddsPapi odds (decimal format)
    team_a_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    team_b_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Implied probabilities
    team_a_implied_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    team_b_implied_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Polymarket CLOB prices
    best_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    fixture: Mapped[Fixture] = relationship("Fixture", back_populates="odds_snapshots")

    __table_args__ = (
        Index("ix_odds_snapshots_fixture_ts", "fixture_id", "ts"),
        Index("ix_odds_snapshots_source", "source"),
    )


class ShadowOrder(Base):
    """Paper trade decision record (append-only)."""

    __tablename__ = "shadow_orders"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    mapping_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mappings.id"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    side: Mapped[str] = mapped_column(String, nullable=False)  # "buy_a" | "buy_b"
    pinnacle_prob: Mapped[float] = mapped_column(Float, nullable=False)
    polymarket_price: Mapped[float] = mapped_column(Float, nullable=False)
    gap: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    mapping: Mapped[Mapping] = relationship("Mapping", back_populates="shadow_orders")

    __table_args__ = (
        Index("ix_shadow_orders_mapping_ts", "mapping_id", "ts"),
        Index("ix_shadow_orders_ts", "ts"),
    )
