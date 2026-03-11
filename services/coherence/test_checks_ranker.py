from coherence.checks import (
    find_complement_findings,
    find_implication_findings,
    find_monotonicity_violations,
    find_partition_sum_findings,
)
from coherence.models import (
    CandidateEvent,
    DateCascade,
    FamilyType,
    MarketInfo,
    PairStatus,
    PartitionStatus,
    Violation,
)
from coherence.ranker import rank_violations_with_orderbook
from coherence.scanner import assign_families


def _market(question: str, yes: float, no: float, parsed_date: str, yes_id: str, no_id: str) -> MarketInfo:
    from datetime import date

    return MarketInfo(
        market_id=question,
        question=question,
        description="desc",
        yes_price=yes,
        no_price=no,
        end_date=f"{parsed_date}T00:00:00Z",
        event_slug="slug",
        event_id="event-id",
        volume=10_000.0,
        liquidity=1_000.0,
        clob_token_ids=[yes_id, no_id],
        yes_token_id=yes_id,
        no_token_id=no_id,
        parsed_date=date.fromisoformat(parsed_date),
    )


def test_find_monotonicity_violations_detects_and_ranks() -> None:
    short = _market("by mar", yes=0.45, no=0.55, parsed_date="2026-03-31", yes_id="y1", no_id="n1")
    long = _market("by jun", yes=0.40, no=0.60, parsed_date="2026-06-30", yes_id="y2", no_id="n2")
    cascade = DateCascade(
        event_slug="test-slug",
        event_title="test title",
        markets=[short, long],
        descriptions_consistent=True,
    )

    violations = find_monotonicity_violations([cascade])
    assert len(violations) == 1
    violation = violations[0]
    assert violation.short_market.question == "by mar"
    assert violation.long_market.question == "by jun"
    assert round(violation.pair_cost, 4) == 0.95
    assert round(violation.edge_cents, 2) == 5.00


def test_rank_violations_with_orderbook_prices_no_and_yes_legs() -> None:
    short = _market("by mar", yes=0.45, no=0.55, parsed_date="2026-03-31", yes_id="short_yes", no_id="short_no")
    long = _market("by jun", yes=0.40, no=0.60, parsed_date="2026-06-30", yes_id="long_yes", no_id="long_no")
    violation = Violation(
        cascade_slug="test",
        cascade_title="title",
        short_market=short,
        long_market=long,
        pair_cost=0.95,
        edge_cents=5.0,
        guaranteed=True,
    )

    class FakeClient:
        def get_orderbooks_batch(self, token_ids: list[str]) -> dict[str, dict]:
            assert set(token_ids) == {"short_no", "long_yes"}
            return {
                "short_no": {"asks": [{"price": 0.40, "size": 1000}]},  # No cost = 0.60
                "long_yes": {"asks": [{"price": 0.35, "size": 1000}]},  # Yes cost = 0.35
            }

        def close(self) -> None:
            return None

    ranked = rank_violations_with_orderbook(
        violations=[violation],
        target_sizes_usd=[100],
        min_edge_cents=0.0,
        client=FakeClient(),
    )
    assert len(ranked) == 1
    opp = ranked[0]
    assert opp.filled is True
    assert round(opp.short_no_avg_price, 4) == 0.6
    assert round(opp.long_yes_avg_price, 4) == 0.35
    assert round(opp.edge_cents_post_slippage, 2) == 5.0


def test_find_partition_sum_findings_flags_deviation() -> None:
    event = CandidateEvent(
        event_id="e1",
        event_slug="range-event",
        event_title="Range event",
        markets=[
            _market("Will CPI be under 2%?", yes=0.30, no=0.70, parsed_date="2026-03-31", yes_id="y1", no_id="n1"),
            _market("Will CPI be between 2% and 4%?", yes=0.35, no=0.65, parsed_date="2026-03-31", yes_id="y2", no_id="n2"),
            _market("Will CPI be between 4% and 6%?", yes=0.30, no=0.70, parsed_date="2026-03-31", yes_id="y3", no_id="n3"),
        ],
    )
    assignments = assign_families([event])
    assert assignments[0].family == FamilyType.RANGE_PARTITION

    findings = find_partition_sum_findings(assignments, tolerance=0.05)
    assert len(findings) == 1
    assert findings[0].is_violation is True
    assert findings[0].status == PartitionStatus.VALID_PARTITION
    assert round(findings[0].sum_yes, 2) == 0.95


def test_find_partition_sum_findings_skips_threshold_ladder() -> None:
    event = CandidateEvent(
        event_id="e2",
        event_slug="threshold-event",
        event_title="Threshold event",
        markets=[
            _market("Will CPI be over 2%?", yes=0.30, no=0.70, parsed_date="2026-03-31", yes_id="y1", no_id="n1"),
            _market("Will CPI be over 3%?", yes=0.20, no=0.80, parsed_date="2026-03-31", yes_id="y2", no_id="n2"),
            _market("Will CPI be over 4%?", yes=0.10, no=0.90, parsed_date="2026-03-31", yes_id="y3", no_id="n3"),
        ],
    )
    assignments = assign_families([event])
    findings = find_partition_sum_findings(assignments, tolerance=0.05)
    assert len(findings) == 1
    assert findings[0].status == PartitionStatus.NON_APPLICABLE
    assert findings[0].is_violation is False


def test_find_complement_findings_flags_deviation() -> None:
    event = CandidateEvent(
        event_id="c1",
        event_slug="complement-event",
        event_title="Complement event",
        markets=[
            _market("Will US strike Iran by June 30, 2026?", yes=0.62, no=0.38, parsed_date="2026-06-30", yes_id="y1", no_id="n1"),
            _market(
                "Will US not strike Iran by June 30, 2026?",
                yes=0.46,
                no=0.54,
                parsed_date="2026-06-30",
                yes_id="y2",
                no_id="n2",
            ),
        ],
    )
    assignments = assign_families([event])
    findings = find_complement_findings(assignments, tolerance=0.05)
    assert len(findings) == 1
    assert findings[0].status == PairStatus.VALID_PAIR
    assert findings[0].is_violation is True
    assert round(findings[0].sum_yes, 2) == 1.08


def test_find_implication_findings_flags_gap() -> None:
    event = CandidateEvent(
        event_id="i1",
        event_slug="implication-event",
        event_title="Implication event",
        markets=[
            _market("Will US strike Iran by June 30, 2026?", yes=0.41, no=0.59, parsed_date="2026-06-30", yes_id="y1", no_id="n1"),
            _market(
                "Will US or Israel strike Iran by June 30, 2026?",
                yes=0.35,
                no=0.65,
                parsed_date="2026-06-30",
                yes_id="y2",
                no_id="n2",
            ),
        ],
    )
    assignments = assign_families([event])
    findings = find_implication_findings(assignments, tolerance=0.02)
    assert len(findings) == 1
    assert findings[0].status == PairStatus.VALID_PAIR
    assert findings[0].is_violation is True
    assert round(findings[0].gap, 2) == 0.06

