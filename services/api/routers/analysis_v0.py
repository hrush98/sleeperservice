"""Read-only V0 analysis endpoints."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from services.api.analysis_v0 import MarketNotFoundError, V0AnalysisService
from services.api.dependencies import enforce_public_api_access, get_v0_analysis_service

router = APIRouter(
    prefix="/v0",
    tags=["Analysis V0"],
    dependencies=[Depends(enforce_public_api_access)],
)


class ResponseMetadata(BaseModel):
    generated_at: str
    trace_included: bool
    artifacts: dict[str, Any] | None = None
    total_scanned: int | None = None


class PriceSnapshot(BaseModel):
    bid: float | None = None
    ask: float | None = None
    midpoint: float | None = None
    spread_cents: float | None = None
    as_of: str


class MarketState(BaseModel):
    price_velocity_15m_points: float | None = None
    price_velocity_1h_points: float | None = None
    volume_1h_usd: float | None = None
    volume_zscore_1h: float | None = None
    depth_near_mid_usd: float | None = None
    book_imbalance_ratio: float | None = None
    volatility_regime: str
    state_summary: str
    as_of: str


class EdgeValue(BaseModel):
    metric: str
    value: float
    basis: str
    suggested_side: Literal["yes", "no"]


class CalibrationContext(BaseModel):
    venue: str
    price_bucket: str
    time_to_resolution_bucket: str
    historical_trade_count: int
    historical_market_count: int
    avg_implied_probability: float
    realized_win_rate: float
    avg_miscalibration: float
    mean_squared_error: float


class MakerTakerContext(BaseModel):
    role_basis: str
    maker_avg_pnl_per_contract: float | None = None
    taker_avg_pnl_per_contract: float | None = None
    maker_trade_count: int | None = None
    taker_trade_count: int | None = None
    summary: str


class SizingContext(BaseModel):
    role_basis: str
    maker_taker_role: str
    recommended_haircut_multiplier: float
    promotion_status: str
    edge_to_noise_ratio: float
    trade_count: int
    summary: str


class EvidenceTraceItem(BaseModel):
    kind: str
    summary: str
    source: str
    metrics: dict[str, Any]


class CoherenceFinding(BaseModel):
    kind: str
    related_market_id: str
    deviation: float
    tolerance: float
    summary: str


class OpportunityItem(BaseModel):
    market_id: str
    slug: str
    title: str
    category: str
    opportunity_type: str
    suggested_side: Literal["yes", "no"]
    price_snapshot: PriceSnapshot
    edge: EdgeValue
    confidence: float
    explanation_summary: str
    market_state: MarketState
    calibration_context: CalibrationContext | None = None
    maker_taker_context: MakerTakerContext | None = None
    sizing_context: SizingContext | None = None
    warnings: list[str] = Field(default_factory=list)
    evidence_trace: list[EvidenceTraceItem] = Field(default_factory=list)


class RankedOpportunitiesResponse(BaseModel):
    opportunities: list[OpportunityItem]
    warnings: list[str] = Field(default_factory=list)
    metadata: ResponseMetadata


class MarketAnalysisResponse(BaseModel):
    market_id: str
    slug: str
    title: str
    category: str
    price_snapshot: PriceSnapshot
    market_state: MarketState
    coherence_findings: list[CoherenceFinding] = Field(default_factory=list)
    calibration_context: CalibrationContext | None = None
    maker_taker_context: MakerTakerContext | None = None
    sizing_context: SizingContext | None = None
    warnings: list[str] = Field(default_factory=list)
    evidence_trace: list[EvidenceTraceItem] = Field(default_factory=list)
    metadata: ResponseMetadata


@router.get("/analysis/opportunities", response_model=RankedOpportunitiesResponse)
def get_ranked_opportunities(
    limit: int = Query(10, ge=1, le=50),
    categories: str | None = Query(None),
    opportunity_types: str | None = Query(None),
    min_confidence: float = Query(0.5, ge=0.0, le=1.0),
    min_edge_value: float = Query(0.0, ge=0.0),
    time_horizon: Literal["24h", "7d", "30d"] | None = Query(None),
    include_trace: bool = Query(False),
    service: V0AnalysisService = Depends(get_v0_analysis_service),
):
    return service.list_ranked_opportunities(
        limit=limit,
        categories=_parse_csv_set(categories),
        opportunity_types=_parse_csv_set(opportunity_types),
        min_confidence=min_confidence,
        min_edge_value=min_edge_value,
        time_horizon=time_horizon,
        include_trace=include_trace,
    )


@router.get("/markets/{market_id}/analysis", response_model=MarketAnalysisResponse)
def get_market_analysis(
    market_id: str,
    include_trace: bool = Query(False),
    service: V0AnalysisService = Depends(get_v0_analysis_service),
):
    try:
        return service.get_market_analysis(
            market_id,
            include_trace=include_trace,
        )
    except MarketNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _parse_csv_set(raw: str | None) -> set[str] | None:
    if raw is None:
        return None
    values = {item.strip().lower() for item in raw.split(",") if item.strip()}
    return values or None
