from datetime import datetime
from typing import Any

from pydantic import BaseModel


class MarketListItem(BaseModel):
    id: str
    platform: str
    platform_market_id: str
    title: str
    status: str | None
    open_time: datetime | None
    close_time: datetime | None

    class Config:
        from_attributes = True


class OutcomeResponse(BaseModel):
    id: str
    outcome_name: str
    platform_outcome_id: str | None
    raw_json: dict | None

    class Config:
        from_attributes = True


class SettlementSpecResponse(BaseModel):
    id: str
    source: str | None
    resolution_time: datetime | None
    criteria_text: str | None
    spec_version_hash: str
    raw_json: dict | None
    created_at: datetime

    class Config:
        from_attributes = True


class QuoteSnapshotResponse(BaseModel):
    id: str
    outcome_id: str | None
    ts: datetime
    price: float | None
    volume_24h: float | None
    liquidity: float | None
    raw_json: dict

    class Config:
        from_attributes = True


class MarketDetailResponse(BaseModel):
    id: str
    platform: str
    platform_market_id: str
    title: str
    description: str | None
    url: str | None
    status: str | None
    open_time: datetime | None
    close_time: datetime | None
    raw_json: dict
    outcomes: list[OutcomeResponse]
    latest_settlement_spec: SettlementSpecResponse | None

    class Config:
        from_attributes = True


class MarketListResponse(BaseModel):
    items: list[MarketListItem]
    limit: int
    offset: int


class QuoteListResponse(BaseModel):
    items: list[QuoteSnapshotResponse]
    limit: int


class DerivedMetricResponse(BaseModel):
    market_id: str
    ts: datetime
    quality_score: float | None
    move_24h: float | None
    interesting_score: float | None
    components: dict | None

    class Config:
        from_attributes = True


class ExternalMatchResponse(BaseModel):
    id: str
    source: str
    external_match_id: str
    league: str | None
    start_time: datetime | None
    team_a: str
    team_b: str
    raw_json: dict
    updated_at: datetime

    class Config:
        from_attributes = True


class ExternalMatchListResponse(BaseModel):
    items: list[ExternalMatchResponse]
    limit: int
    offset: int


class ExternalOddsSnapshotResponse(BaseModel):
    id: str
    external_match_id: str
    ts: datetime
    market_type: str
    selection: str
    odds_decimal: float | None
    odds_american: int | None
    implied_prob: float | None
    raw_json: dict

    class Config:
        from_attributes = True


class ExternalOddsListResponse(BaseModel):
    items: list[ExternalOddsSnapshotResponse]
    limit: int


class ExternalPolymarketMappingResponse(BaseModel):
    id: str
    source: str
    external_match_id: str
    polymarket_market_id: str
    polymarket_outcome_id: str | None
    mapping_confidence: float | None
    mapping_method: str
    raw_json: dict | None
    updated_at: datetime

    class Config:
        from_attributes = True


class MappingListResponse(BaseModel):
    items: list[ExternalPolymarketMappingResponse]
    limit: int
    offset: int


class DisagreementEventResponse(BaseModel):
    id: str
    ts: datetime
    external_match_id: str
    polymarket_market_id: str
    polymarket_outcome_id: str
    ref_implied_prob: float
    poly_mid: float | None
    poly_best_bid: float | None
    poly_best_ask: float | None
    gap: float
    edge: float | None
    raw_json: dict

    class Config:
        from_attributes = True


class DisagreementListResponse(BaseModel):
    items: list[DisagreementEventResponse]
    limit: int


class ShadowOrderResponse(BaseModel):
    id: str
    ts: datetime
    external_match_id: str
    polymarket_market_id: str
    polymarket_outcome_id: str
    side: str
    price: float | None
    size: float | None
    reason: str
    raw_json: dict

    class Config:
        from_attributes = True


class ShadowOrderListResponse(BaseModel):
    items: list[ShadowOrderResponse]
    limit: int
