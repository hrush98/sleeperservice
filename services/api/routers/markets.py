from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.schemas import (
    DerivedMetricResponse,
    MarketDetailResponse,
    MarketListResponse,
    QuoteListResponse,
)
from shared.models import DerivedMetric, Market, Outcome, QuoteSnapshot, SettlementSpec

router = APIRouter()


@router.get("/markets", response_model=MarketListResponse)
def list_markets(
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Market).order_by(Market.updated_at.desc()).offset(offset).limit(limit)
    if status:
        stmt = stmt.where(Market.status == status)
    markets = db.execute(stmt).scalars().all()
    return {"items": markets, "limit": limit, "offset": offset}


@router.get("/markets/{market_id}", response_model=MarketDetailResponse)
def get_market(market_id: str, db: Session = Depends(get_db)):
    market = db.get(Market, market_id)
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found")

    outcomes = db.execute(
        select(Outcome).where(Outcome.market_id == market_id).order_by(Outcome.outcome_name.asc())
    ).scalars().all()

    latest_spec = db.execute(
        select(SettlementSpec)
        .where(SettlementSpec.market_id == market_id)
        .order_by(SettlementSpec.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    return {
        "id": market.id,
        "platform": market.platform,
        "platform_market_id": market.platform_market_id,
        "title": market.title,
        "description": market.description,
        "url": market.url,
        "status": market.status,
        "open_time": market.open_time,
        "close_time": market.close_time,
        "raw_json": market.raw_json,
        "outcomes": outcomes,
        "latest_settlement_spec": latest_spec,
    }


@router.get("/markets/{market_id}/quotes", response_model=QuoteListResponse)
def get_market_quotes(
    market_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    market = db.get(Market, market_id)
    if market is None:
        raise HTTPException(status_code=404, detail="Market not found")

    quotes = db.execute(
        select(QuoteSnapshot)
        .where(QuoteSnapshot.market_id == market_id)
        .order_by(QuoteSnapshot.ts.desc())
        .limit(limit)
    ).scalars().all()
    return {"items": quotes, "limit": limit}


@router.get("/markets/{market_id}/metrics", response_model=DerivedMetricResponse)
def get_market_metrics(market_id: str, db: Session = Depends(get_db)):
    metrics = db.get(DerivedMetric, market_id)
    if metrics is None:
        raise HTTPException(status_code=404, detail="Metrics not found")
    return metrics
