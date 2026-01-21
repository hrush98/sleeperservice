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
