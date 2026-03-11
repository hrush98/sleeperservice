"""Typed models for coherence scanning and family-based checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
from typing import Any


@dataclass(slots=True)
class MarketInfo:
    """Normalized Polymarket market record used by coherence flows."""

    market_id: str
    question: str
    description: str
    yes_price: float
    no_price: float
    end_date: str
    event_slug: str
    event_id: str
    volume: float
    liquidity: float
    clob_token_ids: list[str]
    yes_token_id: str = ""
    no_token_id: str = ""
    parsed_date: date | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["parsed_date"] = self.parsed_date.isoformat() if self.parsed_date else None
        return payload


@dataclass(slots=True)
class CandidateEvent:
    """Event containing potentially coherent Yes/No date markets."""

    event_id: str
    event_slug: str
    event_title: str
    markets: list[MarketInfo]

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_slug": self.event_slug,
            "event_title": self.event_title,
            "markets": [market.to_dict() for market in self.markets],
        }


class FamilyType(str, Enum):
    """Family classifier labels used by discovery routing."""

    BY_CASCADE = "BY_CASCADE"
    ON_PARTITION = "ON_PARTITION"
    RANGE_PARTITION = "RANGE_PARTITION"
    BINARY_COMPLEMENT_PAIR = "BINARY_COMPLEMENT_PAIR"
    IMPLICATION_PAIR = "IMPLICATION_PAIR"
    JOINT_TRIPLE = "JOINT_TRIPLE"
    MIXED_HYBRID = "MIXED_HYBRID"


class StrategyType(str, Enum):
    """Strategy lanes that run after family assignment."""

    S1_MONOTONICITY = "S1_MONOTONICITY"
    S_PARTITION_SUM = "S_PARTITION_SUM"
    S_COMPLEMENT = "S_COMPLEMENT"
    S2_IMPLICATION = "S2_IMPLICATION"
    S3_FRECHET = "S3_FRECHET"
    S_STUB = "S_STUB"


@dataclass(slots=True)
class FamilyAssignment:
    """In-memory family assignment for one normalized event bucket."""

    event_id: str
    event_slug: str
    event_title: str
    parent_event_id: str
    group_id: str
    group_key: str
    family: FamilyType
    markets: list[MarketInfo]
    confidence: float
    semantic_fallback_used: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_slug": self.event_slug,
            "event_title": self.event_title,
            "parent_event_id": self.parent_event_id,
            "group_id": self.group_id,
            "group_key": self.group_key,
            "family": self.family.value,
            "confidence": self.confidence,
            "semantic_fallback_used": self.semantic_fallback_used,
            "notes": self.notes,
            "market_ids": [market.market_id for market in self.markets],
            "markets": [market.to_dict() for market in self.markets],
        }


@dataclass(slots=True)
class StrategyCandidate:
    """Routed strategy candidate from a family assignment."""

    assignment: FamilyAssignment
    strategy: StrategyType
    ready: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment": self.assignment.to_dict(),
            "strategy": self.strategy.value,
            "ready": self.ready,
            "reason": self.reason,
        }


@dataclass(slots=True)
class DateCascade:
    """Date-sorted market family sharing one event-level date-stem question."""

    event_slug: str
    event_title: str
    markets: list[MarketInfo]
    descriptions_consistent: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_slug": self.event_slug,
            "event_title": self.event_title,
            "markets": [market.to_dict() for market in self.markets],
            "descriptions_consistent": self.descriptions_consistent,
        }


@dataclass(slots=True)
class Violation:
    """Monotonicity violation between an earlier and later market."""

    cascade_slug: str
    cascade_title: str
    short_market: MarketInfo
    long_market: MarketInfo
    pair_cost: float
    edge_cents: float
    guaranteed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "cascade_slug": self.cascade_slug,
            "cascade_title": self.cascade_title,
            "short_market": self.short_market.to_dict(),
            "long_market": self.long_market.to_dict(),
            "pair_cost": self.pair_cost,
            "edge_cents": self.edge_cents,
            "guaranteed": self.guaranteed,
        }


class PartitionStatus(str, Enum):
    """Validity status for ON/RANGE partition-style groups."""

    VALID_PARTITION = "VALID_PARTITION"
    NON_APPLICABLE = "NON_APPLICABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(slots=True)
class PartitionFinding:
    """Partition sum check result for ON/RANGE families."""

    event_slug: str
    event_title: str
    family: FamilyType
    strategy: StrategyType
    status: PartitionStatus
    reason: str
    market_count: int
    sum_yes: float
    deviation: float
    tolerance: float
    is_violation: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_slug": self.event_slug,
            "event_title": self.event_title,
            "family": self.family.value,
            "strategy": self.strategy.value,
            "status": self.status.value,
            "reason": self.reason,
            "market_count": self.market_count,
            "sum_yes": self.sum_yes,
            "deviation": self.deviation,
            "tolerance": self.tolerance,
            "is_violation": self.is_violation,
        }


class PairStatus(str, Enum):
    """Validity status for complement/implication pair checks."""

    VALID_PAIR = "VALID_PAIR"
    NON_APPLICABLE = "NON_APPLICABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(slots=True)
class ComplementFinding:
    """Complement pair check result for BINARY_COMPLEMENT_PAIR family."""

    event_slug: str
    event_title: str
    family: FamilyType
    strategy: StrategyType
    status: PairStatus
    reason: str
    market_a: MarketInfo
    market_b: MarketInfo
    sum_yes: float
    deviation: float
    tolerance: float
    is_violation: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_slug": self.event_slug,
            "event_title": self.event_title,
            "family": self.family.value,
            "strategy": self.strategy.value,
            "status": self.status.value,
            "reason": self.reason,
            "market_a": self.market_a.to_dict(),
            "market_b": self.market_b.to_dict(),
            "sum_yes": self.sum_yes,
            "deviation": self.deviation,
            "tolerance": self.tolerance,
            "is_violation": self.is_violation,
        }


@dataclass(slots=True)
class ImplicationFinding:
    """Implication bound check result for IMPLICATION_PAIR family."""

    event_slug: str
    event_title: str
    family: FamilyType
    strategy: StrategyType
    status: PairStatus
    reason: str
    antecedent_market: MarketInfo
    consequent_market: MarketInfo
    gap: float
    tolerance: float
    is_violation: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_slug": self.event_slug,
            "event_title": self.event_title,
            "family": self.family.value,
            "strategy": self.strategy.value,
            "status": self.status.value,
            "reason": self.reason,
            "antecedent_market": self.antecedent_market.to_dict(),
            "consequent_market": self.consequent_market.to_dict(),
            "gap": self.gap,
            "tolerance": self.tolerance,
            "is_violation": self.is_violation,
        }


@dataclass(slots=True)
class RankedOpportunity:
    """M3 opportunity with post-slippage checks."""

    violation: Violation
    target_size_usd: float
    short_no_avg_price: float
    long_yes_avg_price: float
    edge_cents_post_slippage: float
    filled: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "violation": self.violation.to_dict(),
            "target_size_usd": self.target_size_usd,
            "short_no_avg_price": self.short_no_avg_price,
            "long_yes_avg_price": self.long_yes_avg_price,
            "edge_cents_post_slippage": self.edge_cents_post_slippage,
            "filled": self.filled,
        }

