"""
LoL Lead-Lag Arbitrage Bot - Database Models (v2 simplified schema)

Core tables:
- leagues: Tournament/league metadata from both sources
- teams: Team/participant cache from both sources
- fixtures: Upcoming/live matches from both sources
- mappings: OddsPapi fixture ↔ Polymarket fixture links
- odds_snapshots: Live price time series (append-only)
- shadow_orders: Paper trade decisions (append-only, legacy)
- positions: Trade lifecycle (paper/real)
- trade_events: Trade decision/execution event tape (append-only)

Gold-edge tables:
- game_snapshots: Live in-game + PM book snapshots (append-only)
- game_results: Final game outcomes (upsert)
- gold_edge_trades: Hold-to-resolution strategy trades (append + updates)
"""

# pylint: disable=not-callable

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from services.shared.db import Base


class League(Base):
    """League/tournament metadata from OddsPapi or Polymarket."""

    __tablename__ = "leagues"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(String, nullable=False)  # "oddspapi" | "polymarket"
    source_id: Mapped[str] = mapped_column(String, nullable=False)  # tournament_id or series_id
    sport: Mapped[str | None] = mapped_column(String, nullable=True)  # "lol" | "cs2"
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
    market_type: Mapped[str | None] = mapped_column(String, nullable=True)  # match_winner|game_winner|totals
    game_number: Mapped[int | None] = mapped_column(nullable=True)  # 1/2/3 for game_winner
    line_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # totals line, e.g. 3.5
    series_type: Mapped[str | None] = mapped_column(String, nullable=True)  # bo1|bo3|bo5
    parent_fixture_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fixtures.id"), nullable=True
    )
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    league: Mapped[League | None] = relationship("League", back_populates="fixtures")
    team_a: Mapped[Team | None] = relationship("Team", foreign_keys=[team_a_id])
    team_b: Mapped[Team | None] = relationship("Team", foreign_keys=[team_b_id])
    parent_fixture: Mapped[Fixture | None] = relationship(
        "Fixture",
        remote_side="Fixture.id",
        back_populates="child_fixtures",
    )
    child_fixtures: Mapped[list[Fixture]] = relationship(
        "Fixture",
        back_populates="parent_fixture",
    )
    odds_snapshots: Mapped[list[OddsSnapshot]] = relationship("OddsSnapshot", back_populates="fixture")

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_fixtures_source_id"),
        Index("ix_fixtures_source", "source"),
        Index("ix_fixtures_status", "status"),
        Index("ix_fixtures_start_time", "start_time"),
        Index("ix_fixtures_league_id", "league_id"),
        Index("ix_fixtures_market_type", "market_type"),
        Index("ix_fixtures_market_type_line_value", "market_type", "line_value"),
        Index("ix_fixtures_parent_fixture_id", "parent_fixture_id"),
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


class Position(Base):
    """Trade lifecycle record (entry → exit), paper or real."""

    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    mapping_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mappings.id"), nullable=False
    )
    pm_fixture_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fixtures.id"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String, nullable=False, default="paper")
    strategy: Mapped[str] = mapped_column(String, nullable=False, default="lead_lag")
    venue: Mapped[str | None] = mapped_column(String, nullable=True)
    market_type: Mapped[str] = mapped_column(String, nullable=False)
    game_number: Mapped[int | None] = mapped_column(nullable=True)
    side: Mapped[str] = mapped_column(String, nullable=False)  # "A" | "B"

    # Entry
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    entry_p_ref: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_alpha: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    trigger_type: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="confirmed")
    external_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    external_status: Mapped[str | None] = mapped_column(String, nullable=True)

    # Exit
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_p_ref: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    # Calculated
    pnl_absolute: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    hold_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    convergence_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    edge_capture: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    mapping: Mapped[Mapping] = relationship("Mapping")
    pm_fixture: Mapped[Fixture | None] = relationship("Fixture", foreign_keys=[pm_fixture_id])

    __table_args__ = (
        Index("ix_positions_mapping_opened", "mapping_id", "opened_at"),
        Index("ix_positions_strategy_opened", "strategy", "opened_at"),
        Index("ix_positions_opened", "opened_at"),
        Index("ix_positions_closed", "closed_at"),
    )


class OrderAttempt(Base):
    """Execution attempt tracking for live orders (entry/exit)."""

    __tablename__ = "order_attempts"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    position_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    strategy: Mapped[str] = mapped_column(String, nullable=False, default="lead_lag")

    phase: Mapped[str] = mapped_column(String, nullable=False)  # "entry" | "exit"
    side: Mapped[str] = mapped_column(String, nullable=False)  # "BUY" | "SELL"
    token_id: Mapped[str] = mapped_column(String, nullable=False)
    attempt_seq: Mapped[int] = mapped_column(Integer, nullable=False)

    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    requested_size: Mapped[float | None] = mapped_column(Float, nullable=True)

    external_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    external_status: Mapped[str | None] = mapped_column(String, nullable=True)
    matched_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    not_found_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    final_state: Mapped[str | None] = mapped_column(String, nullable=True)
    final_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    position: Mapped[Position] = relationship("Position")

    __table_args__ = (
        Index(
            "ix_order_attempts_position_phase_seq",
            "position_id",
            "phase",
            "attempt_seq",
            unique=True,
        ),
        Index(
            "ix_order_attempts_pending",
            "finalized_at",
            postgresql_where=text("finalized_at IS NULL"),
        ),
        Index("ix_order_attempts_external_order_id", "external_order_id"),
        Index("ix_order_attempts_strategy_submitted", "strategy", "submitted_at"),
    )


class TradeEvent(Base):
    """Append-only trade event tape for decisions and execution lifecycle."""

    __tablename__ = "trade_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    strategy: Mapped[str] = mapped_column(String, nullable=False, default="lead_lag")

    mapping_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mappings.id"), nullable=True
    )
    pm_fixture_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fixtures.id"), nullable=True
    )
    position_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id"), nullable=True
    )

    market_type: Mapped[str | None] = mapped_column(String, nullable=True)
    game_number: Mapped[int | None] = mapped_column(nullable=True)
    side: Mapped[str | None] = mapped_column(String, nullable=True)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)

    edge_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    alpha_spread_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_epsilon: Mapped[float | None] = mapped_column(Float, nullable=True)

    p_ref_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_ref_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    mid_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    bid_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    mid_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_side: Mapped[str | None] = mapped_column(String, nullable=True)

    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_available: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    convergence_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    external_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    external_fill_id: Mapped[str | None] = mapped_column(String, nullable=True)
    external_status: Mapped[str | None] = mapped_column(String, nullable=True)

    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    mapping: Mapped[Mapping | None] = relationship("Mapping")
    pm_fixture: Mapped[Fixture | None] = relationship("Fixture", foreign_keys=[pm_fixture_id])
    position: Mapped[Position | None] = relationship("Position")

    __table_args__ = (
        Index("ix_trade_events_mapping_ts", "mapping_id", "ts"),
        Index("ix_trade_events_position_ts", "position_id", "ts"),
        Index("ix_trade_events_run_ts", "run_id", "ts"),
        Index("ix_trade_events_strategy_ts", "strategy", "ts"),
    )


class ComplementArb(Base):
    """Two-leg binary complement arb lifecycle record."""

    __tablename__ = "complement_arbs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    mapping_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mappings.id"), nullable=False
    )
    pm_fixture_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fixtures.id"), nullable=True
    )
    market_type: Mapped[str] = mapped_column(String, nullable=False)
    game_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="IDLE")

    leg_a_position_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id"), nullable=True
    )
    leg_b_position_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("positions.id"), nullable=True
    )

    vwap_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    vwap_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    locked_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_cost_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    mapping: Mapped[Mapping] = relationship("Mapping")
    pm_fixture: Mapped[Fixture | None] = relationship("Fixture", foreign_keys=[pm_fixture_id])
    leg_a_position: Mapped[Position | None] = relationship("Position", foreign_keys=[leg_a_position_id])
    leg_b_position: Mapped[Position | None] = relationship("Position", foreign_keys=[leg_b_position_id])

    __table_args__ = (
        Index("ix_complement_arbs_mapping_created", "mapping_id", "created_at"),
        Index("ix_complement_arbs_state_created", "state", "created_at"),
        Index("ix_complement_arbs_run_created", "run_id", "created_at"),
    )


class GameSnapshot(Base):
    """Append-only live game snapshot rows for gold-edge strategy."""

    __tablename__ = "game_snapshots"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    source_match_id: Mapped[str] = mapped_column(String, nullable=False)  # Goalserve @id
    source_league: Mapped[str | None] = mapped_column(String, nullable=True)
    source_date: Mapped[str | None] = mapped_column(String, nullable=True)
    source_time: Mapped[str | None] = mapped_column(String, nullable=True)
    game_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    team_a_name: Mapped[str] = mapped_column(String, nullable=False)
    team_b_name: Mapped[str] = mapped_column(String, nullable=False)
    team_a_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    team_b_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    game_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gold_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    gold_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    gold_diff: Mapped[float | None] = mapped_column(Float, nullable=True)
    kills_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    kills_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    towers_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    towers_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    dragons_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    dragons_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    barons_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    barons_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    inhibitors_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    inhibitors_b: Mapped[float | None] = mapped_column(Float, nullable=True)

    pm_fixture_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fixtures.id"), nullable=True
    )
    pm_token_id_a: Mapped[str | None] = mapped_column(String, nullable=True)
    pm_token_id_b: Mapped[str | None] = mapped_column(String, nullable=True)
    pm_bid_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    pm_ask_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    pm_bid_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    pm_ask_b: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    pm_fixture: Mapped[Fixture | None] = relationship("Fixture", foreign_keys=[pm_fixture_id])

    __table_args__ = (
        Index("ix_game_snapshots_source_match_ts", "source_match_id", "ts"),
        Index("ix_game_snapshots_game_no_ts", "game_no", "ts"),
        Index("ix_game_snapshots_pm_fixture_ts", "pm_fixture_id", "ts"),
    )


class GameResult(Base):
    """Final per-game outcomes for Goalserve LoL matches."""

    __tablename__ = "game_results"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_match_id: Mapped[str] = mapped_column(String, nullable=False)
    game_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source_date: Mapped[str | None] = mapped_column(String, nullable=True)
    source_league: Mapped[str | None] = mapped_column(String, nullable=True)

    team_a_name: Mapped[str] = mapped_column(String, nullable=False)
    team_b_name: Mapped[str] = mapped_column(String, nullable=False)
    winner_side: Mapped[str | None] = mapped_column(String, nullable=True)  # "A" | "B"
    final_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    final_gold_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_gold_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_kills_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_kills_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_towers_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_towers_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_dragons_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_dragons_b: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_barons_a: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_barons_b: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("source_match_id", "game_no", name="uq_game_results_match_game"),
        Index("ix_game_results_source_date", "source_date"),
    )


class GoldEdgeTrade(Base):
    """Standalone hold-to-resolution trade lifecycle for gold-edge strategy."""

    __tablename__ = "gold_edge_trades"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)  # paper|live
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")  # open|resolved

    source_match_id: Mapped[str] = mapped_column(String, nullable=False)
    game_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_a_name: Mapped[str] = mapped_column(String, nullable=False)
    team_b_name: Mapped[str] = mapped_column(String, nullable=False)
    picked_side: Mapped[str] = mapped_column(String, nullable=False)  # "A" | "B"

    pm_fixture_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fixtures.id"), nullable=True
    )
    token_id: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    model_prob: Mapped[float] = mapped_column(Float, nullable=False)
    edge_at_entry: Mapped[float] = mapped_column(Float, nullable=False)
    stake_usd: Mapped[float] = mapped_column(Float, nullable=False)
    shares: Mapped[float | None] = mapped_column(Float, nullable=True)

    order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    order_status: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    winner_side: Mapped[str | None] = mapped_column(String, nullable=True)
    pnl_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    pm_fixture: Mapped[Fixture | None] = relationship("Fixture", foreign_keys=[pm_fixture_id])

    __table_args__ = (
        Index("ix_gold_edge_trades_open", "status"),
        Index("ix_gold_edge_trades_ts", "ts"),
        Index("ix_gold_edge_trades_source_match", "source_match_id"),
    )
