from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.db import Base


class Market(Base):
    __tablename__ = "markets"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    platform_market_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    open_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    outcomes: Mapped[list[Outcome]] = relationship("Outcome", back_populates="market")
    settlement_specs: Mapped[list[SettlementSpec]] = relationship(
        "SettlementSpec", back_populates="market"
    )
    quote_snapshots: Mapped[list[QuoteSnapshot]] = relationship(
        "QuoteSnapshot", back_populates="market"
    )

    __table_args__ = (
        UniqueConstraint("platform", "platform_market_id", name="uq_markets_platform_market"),
        Index("ix_markets_platform_market", "platform", "platform_market_id"),
        Index("ix_markets_status", "status"),
        Index("ix_markets_updated_at", "updated_at"),
    )


class Outcome(Base):
    __tablename__ = "outcomes"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    market_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("markets.id"))
    outcome_name: Mapped[str] = mapped_column(String, nullable=False)
    platform_outcome_id: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    market: Mapped[Market] = relationship("Market", back_populates="outcomes")
    quote_snapshots: Mapped[list[QuoteSnapshot]] = relationship(
        "QuoteSnapshot", back_populates="outcome"
    )

    __table_args__ = (
        UniqueConstraint("market_id", "outcome_name", name="uq_outcomes_market_name"),
    )


class SettlementSpec(Base):
    __tablename__ = "settlement_specs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    market_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("markets.id"))
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    resolution_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    criteria_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec_version_hash: Mapped[str] = mapped_column(String, nullable=False)
    raw_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    market: Mapped[Market] = relationship("Market", back_populates="settlement_specs")

    __table_args__ = (
        UniqueConstraint("market_id", "spec_version_hash", name="uq_settlement_spec_version"),
        Index("ix_settlement_specs_market_created", "market_id", "created_at"),
    )


class QuoteSnapshot(Base):
    __tablename__ = "quote_snapshots"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    market_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("markets.id"))
    outcome_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), ForeignKey("outcomes.id"), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    liquidity: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    market: Mapped[Market] = relationship("Market", back_populates="quote_snapshots")
    outcome: Mapped[Outcome | None] = relationship("Outcome", back_populates="quote_snapshots")

    __table_args__ = (
        Index("ix_quote_snapshots_market_ts", "market_id", "ts"),
        Index("ix_quote_snapshots_outcome_ts", "outcome_id", "ts"),
    )


class ExternalMatch(Base):
    __tablename__ = "external_matches"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(String, nullable=False)
    external_match_id: Mapped[str] = mapped_column(String, nullable=False)
    league: Mapped[str | None] = mapped_column(String, nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    team_a: Mapped[str] = mapped_column(String, nullable=False)
    team_b: Mapped[str] = mapped_column(String, nullable=False)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    odds_snapshots: Mapped[list[ExternalOddsSnapshot]] = relationship(
        "ExternalOddsSnapshot", back_populates="external_match"
    )

    __table_args__ = (
        UniqueConstraint("source", "external_match_id", name="uq_external_matches_source_match"),
        Index("ix_external_matches_source_match", "source", "external_match_id"),
        Index("ix_external_matches_start_time", "start_time"),
        Index("ix_external_matches_updated_at", "updated_at"),
    )


class ExternalOddsSnapshot(Base):
    __tablename__ = "external_odds_snapshots"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    external_match_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("external_matches.id"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    market_type: Mapped[str] = mapped_column(String, nullable=False)
    selection: Mapped[str] = mapped_column(String, nullable=False)
    odds_decimal: Mapped[float | None] = mapped_column(Float, nullable=True)
    odds_american: Mapped[int | None] = mapped_column(Integer, nullable=True)
    implied_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    external_match: Mapped[ExternalMatch] = relationship(
        "ExternalMatch", back_populates="odds_snapshots"
    )

    __table_args__ = (
        Index("ix_external_odds_snapshots_match_ts", "external_match_id", "ts"),
        UniqueConstraint(
            "external_match_id",
            "market_type",
            "selection",
            "ts",
            name="uq_external_odds_snapshots_natural",
        ),
    )


class ExternalPolymarketMapping(Base):
    __tablename__ = "external_polymarket_mappings"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    source: Mapped[str] = mapped_column(String, nullable=False)
    external_match_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("external_matches.id"), nullable=False
    )
    polymarket_market_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False
    )
    polymarket_outcome_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outcomes.id"), nullable=True
    )
    mapping_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    mapping_method: Mapped[str] = mapped_column(String, nullable=False)
    raw_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "source",
            "external_match_id",
            "polymarket_market_id",
            "polymarket_outcome_id",
            name="uq_external_polymarket_mapping",
        ),
        Index("ix_external_polymarket_mappings_external", "external_match_id", "updated_at"),
        Index("ix_external_polymarket_mappings_market", "polymarket_market_id", "updated_at"),
    )


class DisagreementEvent(Base):
    __tablename__ = "disagreement_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    external_match_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("external_matches.id"), nullable=False
    )
    polymarket_market_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False
    )
    polymarket_outcome_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outcomes.id"), nullable=False
    )
    ref_implied_prob: Mapped[float] = mapped_column(Float, nullable=False)
    poly_mid: Mapped[float | None] = mapped_column(Float, nullable=True)
    poly_best_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    poly_best_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    gap: Mapped[float] = mapped_column(Float, nullable=False)
    edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_disagreement_events_market_ts", "polymarket_market_id", "ts"),
        Index("ix_disagreement_events_external_ts", "external_match_id", "ts"),
    )


class ShadowOrder(Base):
    __tablename__ = "shadow_orders"

    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    external_match_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("external_matches.id"), nullable=False
    )
    polymarket_market_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("markets.id"), nullable=False
    )
    polymarket_outcome_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("outcomes.id"), nullable=False
    )
    side: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    size: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_shadow_orders_market_ts", "polymarket_market_id", "ts"),
        Index("ix_shadow_orders_external_ts", "external_match_id", "ts"),
    )


class DerivedMetric(Base):
    __tablename__ = "derived_metrics"

    market_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("markets.id"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    move_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    interesting_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    components: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    market: Mapped[Market] = relationship("Market")
