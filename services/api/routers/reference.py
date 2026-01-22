from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.schemas import (
    DisagreementEventResponse,
    DisagreementListResponse,
    ExternalMatchListResponse,
    ExternalOddsListResponse,
    ExternalPolymarketMappingResponse,
    ExternalMatchResponse,
    MappingListResponse,
    ShadowOrderListResponse,
    ShadowOrderResponse,
)
from shared.models import (
    DisagreementEvent,
    ExternalMatch,
    ExternalOddsSnapshot,
    ExternalPolymarketMapping,
    ShadowOrder,
)

router = APIRouter()


@router.get("/external/matches", response_model=ExternalMatchListResponse)
def list_external_matches(
    source: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(ExternalMatch).order_by(ExternalMatch.updated_at.desc()).offset(offset).limit(limit)
    if source:
        stmt = stmt.where(ExternalMatch.source == source)
    items = db.execute(stmt).scalars().all()
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/external/matches/{external_match_pk}", response_model=ExternalMatchResponse)
def get_external_match(external_match_pk: str, db: Session = Depends(get_db)):
    match = db.get(ExternalMatch, external_match_pk)
    if match is None:
        raise HTTPException(status_code=404, detail="External match not found")
    return match


@router.get("/external/matches/{external_match_pk}/odds", response_model=ExternalOddsListResponse)
def list_external_odds(
    external_match_pk: str,
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    match = db.get(ExternalMatch, external_match_pk)
    if match is None:
        raise HTTPException(status_code=404, detail="External match not found")

    stmt = (
        select(ExternalOddsSnapshot)
        .where(ExternalOddsSnapshot.external_match_id == external_match_pk)
        .order_by(ExternalOddsSnapshot.ts.desc())
        .limit(limit)
    )
    items = db.execute(stmt).scalars().all()
    return {"items": items, "limit": limit}


@router.get("/mappings", response_model=MappingListResponse)
def list_mappings(
    source: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = (
        select(ExternalPolymarketMapping)
        .order_by(ExternalPolymarketMapping.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if source:
        stmt = stmt.where(ExternalPolymarketMapping.source == source)
    items = db.execute(stmt).scalars().all()
    return {"items": items, "limit": limit, "offset": offset}


@router.get("/mappings/{mapping_id}", response_model=ExternalPolymarketMappingResponse)
def get_mapping(mapping_id: str, db: Session = Depends(get_db)):
    mapping = db.get(ExternalPolymarketMapping, mapping_id)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return mapping


@router.get("/disagreements", response_model=DisagreementListResponse)
def list_disagreements(
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    items = db.execute(select(DisagreementEvent).order_by(DisagreementEvent.ts.desc()).limit(limit)).scalars().all()
    return {"items": items, "limit": limit}


@router.get("/disagreements/{event_id}", response_model=DisagreementEventResponse)
def get_disagreement(event_id: str, db: Session = Depends(get_db)):
    event = db.get(DisagreementEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Disagreement event not found")
    return event


@router.get("/shadow-orders", response_model=ShadowOrderListResponse)
def list_shadow_orders(
    limit: int = Query(default=200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    items = db.execute(select(ShadowOrder).order_by(ShadowOrder.ts.desc()).limit(limit)).scalars().all()
    return {"items": items, "limit": limit}


@router.get("/shadow-orders/{order_id}", response_model=ShadowOrderResponse)
def get_shadow_order(order_id: str, db: Session = Depends(get_db)):
    order = db.get(ShadowOrder, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Shadow order not found")
    return order


