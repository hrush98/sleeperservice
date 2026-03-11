from coherence.models import FamilyType, StrategyType
from coherence.scanner import (
    _filter_candidate_events,
    assign_families,
    route_strategy_candidates,
    scan_market_catalog,
)


def _yes_no_market(question: str) -> dict:
    return {
        "id": question,
        "question": question,
        "description": "same rule",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.40", "0.60"]',
        "endDate": "2026-06-01T00:00:00Z",
        "active": True,
        "closed": False,
        "volume": "1500",
        "liquidity": "100",
        "clobTokenIds": '["a", "b"]',
    }


def test_filter_candidate_events_requires_two_markets() -> None:
    events = [
        {
            "id": "e1",
            "slug": "s1",
            "title": "event",
            "markets": [_yes_no_market("Q1 by March 1, 2026?")],
        }
    ]
    assert _filter_candidate_events(events) == []


def test_filter_candidate_events_skips_multi_outcome() -> None:
    events = [
        {
            "id": "e1",
            "slug": "s1",
            "title": "event",
            "markets": [
                _yes_no_market("Q1 by March 1, 2026?"),
                {
                    **_yes_no_market("Q1 by April 1, 2026?"),
                    "outcomes": '["A", "B", "C"]',
                },
            ],
        }
    ]
    assert _filter_candidate_events(events) == []


def test_scan_market_catalog_paginates() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        def _gamma_request(self, endpoint: str, params: dict) -> list[dict]:
            assert endpoint == "/events"
            offset = params["offset"]
            self.calls += 1
            if offset == 0:
                return [
                    {
                        "id": "e1",
                        "slug": "s1",
                        "title": "event one",
                        "markets": [
                            _yes_no_market("US strikes Iran by March 31, 2026?"),
                            _yes_no_market("US strikes Iran by June 30, 2026?"),
                        ],
                    }
                ]
            return []

        def close(self) -> None:
            return None

    events, candidates = scan_market_catalog(client=FakeClient(), limit=100)
    assert len(events) == 1
    assert len(candidates) == 1
    assert len(candidates[0].markets) == 2


def test_assign_families_detects_by_cascade() -> None:
    candidates = _filter_candidate_events(
        [
            {
                "id": "e1",
                "slug": "s1",
                "title": "event one",
                "markets": [
                    _yes_no_market("US strikes Iran by March 31, 2026?"),
                    _yes_no_market("US strikes Iran by June 30, 2026?"),
                ],
            }
        ]
    )
    assignments = assign_families(candidates)
    assert len(assignments) == 1
    assert assignments[0].family == FamilyType.BY_CASCADE


def test_assign_families_detects_on_partition() -> None:
    candidates = _filter_candidate_events(
        [
            {
                "id": "e1",
                "slug": "s1",
                "title": "event one",
                "markets": [
                    _yes_no_market("Will X happen on March 31, 2026?"),
                    _yes_no_market("Will X happen on April 1, 2026?"),
                ],
            }
        ]
    )
    assignments = assign_families(candidates)
    assert len(assignments) == 1
    assert assignments[0].family == FamilyType.ON_PARTITION


def test_assign_families_detects_range_partition() -> None:
    candidates = _filter_candidate_events(
        [
            {
                "id": "e1",
                "slug": "s1",
                "title": "event one",
                "markets": [
                    _yes_no_market("Will CPI be under 2%?"),
                    _yes_no_market("Will CPI be between 2% and 4%?"),
                ],
            }
        ]
    )
    assignments = assign_families(candidates)
    assert len(assignments) == 1
    assert assignments[0].family == FamilyType.RANGE_PARTITION


def test_route_strategy_candidates_maps_enabled_lanes() -> None:
    candidates = _filter_candidate_events(
        [
            {
                "id": "by",
                "slug": "by",
                "title": "by event",
                "markets": [
                    _yes_no_market("Will A happen by March 31, 2026?"),
                    _yes_no_market("Will A happen by June 30, 2026?"),
                ],
            },
            {
                "id": "on",
                "slug": "on",
                "title": "on event",
                "markets": [
                    _yes_no_market("Will B happen on March 31, 2026?"),
                    _yes_no_market("Will B happen on April 1, 2026?"),
                ],
            },
        ]
    )
    routed = route_strategy_candidates(assign_families(candidates))
    assert [item.strategy for item in routed] == [StrategyType.S1_MONOTONICITY, StrategyType.S_PARTITION_SUM]
    assert all(item.ready for item in routed)


def test_assign_families_detects_complement_pair() -> None:
    candidates = _filter_candidate_events(
        [
            {
                "id": "c1",
                "slug": "complement",
                "title": "complement event",
                "markets": [
                    _yes_no_market("Will US strike Iran by June 30, 2026?"),
                    _yes_no_market("Will US not strike Iran by June 30, 2026?"),
                ],
            }
        ]
    )
    assignments = assign_families(candidates)
    assert len(assignments) == 1
    assert assignments[0].family == FamilyType.BINARY_COMPLEMENT_PAIR
    routed = route_strategy_candidates(assignments)
    assert routed[0].strategy == StrategyType.S_COMPLEMENT
    assert routed[0].ready is True


def test_assign_families_detects_implication_pair() -> None:
    candidates = _filter_candidate_events(
        [
            {
                "id": "i1",
                "slug": "implication",
                "title": "implication event",
                "markets": [
                    _yes_no_market("Will US strike Iran by June 30, 2026?"),
                    _yes_no_market("Will US or Israel strike Iran by June 30, 2026?"),
                ],
            }
        ]
    )
    assignments = assign_families(candidates)
    assert len(assignments) == 1
    assert assignments[0].family == FamilyType.IMPLICATION_PAIR
    assert len(assignments[0].markets) == 2
    assert "Will US strike Iran" in assignments[0].markets[0].question
    assert "Will US or Israel strike Iran" in assignments[0].markets[1].question
    routed = route_strategy_candidates(assignments)
    assert routed[0].strategy == StrategyType.S2_IMPLICATION
    assert routed[0].ready is True


def test_assign_families_decomposes_mixed_event() -> None:
    candidates = _filter_candidate_events(
        [
            {
                "id": "mixed1",
                "slug": "mixed",
                "title": "mixed event",
                "markets": [
                    _yes_no_market("Will X happen by March 31, 2026?"),
                    _yes_no_market("Will X happen by June 30, 2026?"),
                    _yes_no_market("Will X happen on March 31, 2026?"),
                    _yes_no_market("Will X happen on April 1, 2026?"),
                ],
            }
        ]
    )
    assignments = assign_families(candidates)
    assert len(assignments) == 2
    assert {item.family for item in assignments} == {FamilyType.BY_CASCADE, FamilyType.ON_PARTITION}
    assert all(item.parent_event_id == "mixed1" for item in assignments)

