from services.coherence.detector import detect_date_cascades, parse_market_date
from services.coherence.models import CandidateEvent, MarketInfo


def _mk_market(question: str, end_date: str, description: str = "same rule") -> MarketInfo:
    return MarketInfo(
        market_id=question,
        question=question,
        description=description,
        yes_price=0.4,
        no_price=0.6,
        end_date=end_date,
        event_slug="slug",
        event_id="event-id",
        volume=2000.0,
        liquidity=100.0,
        clob_token_ids=["1", "2"],
    )


def test_parse_market_date_prefers_question_date() -> None:
    parsed = parse_market_date(
        "US strikes Iran by February 13, 2026?",
        "2026-03-01T00:00:00Z",
    )
    assert parsed is not None
    assert parsed.isoformat() == "2026-02-13"


def test_parse_market_date_falls_back_to_end_date() -> None:
    parsed = parse_market_date(
        "Will this happen?",
        "2026-05-20T00:00:00Z",
    )
    assert parsed is not None
    assert parsed.isoformat() == "2026-05-20"


def test_detect_date_cascades_filters_stem_mismatch(monkeypatch) -> None:
    monkeypatch.setattr("services.coherence.detector.settings.coherence_semantic_fallback_enabled", False)
    candidate = CandidateEvent(
        event_id="e1",
        event_slug="s1",
        event_title="test event",
        markets=[
            _mk_market("Event A by March 1, 2026?", "2026-03-01T00:00:00Z"),
            _mk_market("Different question by April 1, 2026?", "2026-04-01T00:00:00Z"),
        ],
    )
    cascades, diagnostics = detect_date_cascades([candidate])
    assert cascades == []
    assert diagnostics["stem_mismatch"] == 1


def test_detect_date_cascades_sorts_by_date() -> None:
    candidate = CandidateEvent(
        event_id="e1",
        event_slug="s1",
        event_title="test event",
        markets=[
            _mk_market("US strikes Iran by June 30, 2026?", "2026-06-30T00:00:00Z"),
            _mk_market("US strikes Iran by March 31, 2026?", "2026-03-31T00:00:00Z"),
        ],
    )
    cascades, diagnostics = detect_date_cascades([candidate])
    assert diagnostics["stem_mismatch"] == 0
    assert len(cascades) == 1
    assert [m.parsed_date.isoformat() for m in cascades[0].markets] == ["2026-03-31", "2026-06-30"]


def test_detect_date_cascades_semantic_fallback_allows_mismatch(monkeypatch) -> None:
    monkeypatch.setattr("services.coherence.detector._semantic_series_match", lambda _markets: True)
    candidate = CandidateEvent(
        event_id="e1",
        event_slug="s1",
        event_title="test event",
        markets=[
            _mk_market("Event A by March 1, 2026?", "2026-03-01T00:00:00Z"),
            _mk_market("Different wording by April 1, 2026?", "2026-04-01T00:00:00Z"),
        ],
    )
    cascades, diagnostics = detect_date_cascades([candidate])
    assert len(cascades) == 1
    assert diagnostics["semantic_fallback_used"] == 1
    assert diagnostics["stem_mismatch"] == 0
