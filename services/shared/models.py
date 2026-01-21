from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint, func
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
