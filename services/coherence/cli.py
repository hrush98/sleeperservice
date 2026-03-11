"""CLI entry point for coherence family-first scans."""

from __future__ import annotations

import logging
from typing import Any

import typer

from coherence.checks import (
    find_complement_findings,
    find_implication_findings,
    find_monotonicity_violations,
    find_partition_sum_findings,
)
from coherence.detector import detect_date_cascades
from coherence.ranker import rank_violations_with_orderbook
from coherence.scanner import (
    assign_families,
    decomposition_stats,
    family_counts,
    route_strategy_candidates,
    scan_market_catalog,
    strategy_counts,
    write_catalog_cache,
)
from coherence.models import CandidateEvent, FamilyType, PairStatus, PartitionStatus
from shared.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = typer.Typer(
    name="coherence",
    help="Polymarket coherence scanner (M0 + M1)",
    add_completion=False,
)


@app.callback()
def root() -> None:
    """Coherence command group."""


@app.command()
def scan(
    limit: int = typer.Option(100, "--limit", help="Gamma page size per request"),
    cache_path: str = typer.Option(
        settings.coherence_cache_path,
        "--cache-path",
        help="Path for scanner JSON cache",
    ),
    cache_only: bool = typer.Option(
        False,
        "--cache-only",
        help="Run M0 scanner and cache write only",
    ),
    show_samples: int = typer.Option(3, "--show-samples", help="How many cascades to print"),
    min_edge_cents: float = typer.Option(
        0.0,
        "--min-edge-cents",
        help="Minimum M2/M3 edge in cents to print",
    ),
    m3_sizes: str = typer.Option(
        "100,500,1000",
        "--m3-sizes",
        help="Comma-separated USD target sizes for M3 depth checks",
    ),
    skip_m3: bool = typer.Option(
        False,
        "--skip-m3",
        help="Skip orderbook slippage checks (M3)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logs"),
) -> None:
    """Run one full family-first scan."""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    events, candidates = scan_market_catalog(limit=limit)
    cache_file = write_catalog_cache(events=events, candidates=candidates, cache_path=cache_path)

    typer.echo(f"Events fetched: {len(events)}")
    typer.echo(f"Candidate events: {len(candidates)}")
    typer.echo(f"Cache written: {cache_file}")
    if cache_only:
        return

    assignments = assign_families(candidates)
    routed = route_strategy_candidates(assignments)
    decompose = decomposition_stats(assignments, source_events=len(candidates))
    typer.echo("")
    typer.echo("Decomposition:")
    for key, value in decompose.items():
        typer.echo(f"  - {key}: {value}")
    typer.echo("Family counts:")
    for key, value in sorted(family_counts(assignments).items()):
        typer.echo(f"  - {key}: {value}")
    typer.echo("Routed strategy counts:")
    for key, value in sorted(strategy_counts(routed).items()):
        typer.echo(f"  - {key}: {value}")

    by_candidates = [
        CandidateEvent(
            event_id=item.assignment.event_id,
            event_slug=item.assignment.event_slug,
            event_title=item.assignment.event_title,
            markets=item.assignment.markets,
        )
        for item in routed
        if item.ready and item.assignment.family == FamilyType.BY_CASCADE
    ]
    cascades, diagnostics = detect_date_cascades(by_candidates)
    typer.echo(f"Detected cascades: {len(cascades)}")
    typer.echo("Diagnostics:")
    for key, value in diagnostics.items():
        typer.echo(f"  - {key}: {value}")

    for cascade in cascades[:show_samples]:
        _print_cascade(cascade.to_dict())

    violations = find_monotonicity_violations(cascades)
    violations = [item for item in violations if item.edge_cents >= min_edge_cents]
    typer.echo("")
    typer.echo(f"M2 violations: {len(violations)}")
    for violation in violations[:show_samples]:
        typer.echo(
            "  - {slug} | edge={edge:.2f}c | cost={cost:.4f} | short={short_q} | long={long_q}".format(
                slug=violation.cascade_slug,
                edge=violation.edge_cents,
                cost=violation.pair_cost,
                short_q=violation.short_market.question,
                long_q=violation.long_market.question,
            )
        )

    partition_assignments = [
        item.assignment
        for item in routed
        if item.ready and item.assignment.family in {FamilyType.ON_PARTITION, FamilyType.RANGE_PARTITION}
    ]
    partition_findings = find_partition_sum_findings(partition_assignments)
    partition_valid = [item for item in partition_findings if item.status == PartitionStatus.VALID_PARTITION]
    partition_non_applicable = [item for item in partition_findings if item.status != PartitionStatus.VALID_PARTITION]
    partition_violations = [item for item in partition_valid if item.is_violation]
    typer.echo(f"Partition checks: {len(partition_findings)}")
    typer.echo(f"Partition valid groups: {len(partition_valid)}")
    typer.echo(f"Partition non-applicable groups: {len(partition_non_applicable)}")
    typer.echo(f"Partition hard violations: {len(partition_violations)}")
    for finding in partition_violations[:show_samples]:
        typer.echo(
            "  - {slug} | family={family} | sum_yes={sum_yes:.3f} | deviation={dev:.3f} (tol={tol:.3f})".format(
                slug=finding.event_slug,
                family=finding.family.value,
                sum_yes=finding.sum_yes,
                dev=finding.deviation,
                tol=finding.tolerance,
            )
        )

    complement_assignments = [
        item.assignment for item in routed if item.ready and item.assignment.family == FamilyType.BINARY_COMPLEMENT_PAIR
    ]
    complement_findings = find_complement_findings(complement_assignments)
    complement_valid = [item for item in complement_findings if item.status == PairStatus.VALID_PAIR]
    complement_non_applicable = [item for item in complement_findings if item.status == PairStatus.NON_APPLICABLE]
    complement_insufficient = [item for item in complement_findings if item.status == PairStatus.INSUFFICIENT_EVIDENCE]
    complement_violations = [item for item in complement_valid if item.is_violation]
    typer.echo(f"Complement checks: {len(complement_findings)}")
    typer.echo(f"Complement valid pairs: {len(complement_valid)}")
    typer.echo(f"Complement non-applicable pairs: {len(complement_non_applicable)}")
    typer.echo(f"Complement insufficient-evidence pairs: {len(complement_insufficient)}")
    typer.echo(f"Complement hard violations: {len(complement_violations)}")
    for finding in complement_violations[:show_samples]:
        typer.echo(
            "  - {slug} | sum_yes={sum_yes:.3f} | deviation={dev:.3f} (tol={tol:.3f}) | "
            "A={a} | B={b}".format(
                slug=finding.event_slug,
                sum_yes=finding.sum_yes,
                dev=finding.deviation,
                tol=finding.tolerance,
                a=finding.market_a.question,
                b=finding.market_b.question,
            )
        )

    implication_assignments = [
        item.assignment for item in routed if item.ready and item.assignment.family == FamilyType.IMPLICATION_PAIR
    ]
    implication_findings = find_implication_findings(implication_assignments)
    implication_valid = [item for item in implication_findings if item.status == PairStatus.VALID_PAIR]
    implication_non_applicable = [item for item in implication_findings if item.status == PairStatus.NON_APPLICABLE]
    implication_insufficient = [item for item in implication_findings if item.status == PairStatus.INSUFFICIENT_EVIDENCE]
    implication_violations = [item for item in implication_valid if item.is_violation]
    typer.echo(f"Implication checks: {len(implication_findings)}")
    typer.echo(f"Implication valid pairs: {len(implication_valid)}")
    typer.echo(f"Implication non-applicable pairs: {len(implication_non_applicable)}")
    typer.echo(f"Implication insufficient-evidence pairs: {len(implication_insufficient)}")
    typer.echo(f"Implication hard violations: {len(implication_violations)}")
    for finding in implication_violations[:show_samples]:
        typer.echo(
            "  - {slug} | gap={gap:.3f} (tol={tol:.3f}) | A={a} => B={b}".format(
                slug=finding.event_slug,
                gap=finding.gap,
                tol=finding.tolerance,
                a=finding.antecedent_market.question,
                b=finding.consequent_market.question,
            )
        )

    if skip_m3:
        return
    sizes = _parse_sizes(m3_sizes)
    opportunities = rank_violations_with_orderbook(
        violations=violations,
        target_sizes_usd=sizes,
        min_edge_cents=min_edge_cents,
    )
    typer.echo(f"M3 opportunities: {len(opportunities)}")
    for opportunity in opportunities[:show_samples]:
        typer.echo(
            "  - {slug} | size=${size:.0f} | edge_post={edge:.2f}c | "
            "yes_long={yes:.4f} no_short={no:.4f} | filled={filled}".format(
                slug=opportunity.violation.cascade_slug,
                size=opportunity.target_size_usd,
                edge=opportunity.edge_cents_post_slippage,
                yes=opportunity.long_yes_avg_price,
                no=opportunity.short_no_avg_price,
                filled=opportunity.filled,
            )
        )


def _print_cascade(cascade: dict[str, Any]) -> None:
    typer.echo("")
    typer.echo(f"Cascade: {cascade['event_title']} ({cascade['event_slug']})")
    typer.echo(f"Descriptions consistent: {cascade['descriptions_consistent']}")
    for market in cascade["markets"]:
        typer.echo(
            "  - {date} | yes={yes:.4f} no={no:.4f} | {question}".format(
                date=market.get("parsed_date") or market.get("end_date") or "unknown-date",
                yes=float(market.get("yes_price", 0.0)),
                no=float(market.get("no_price", 0.0)),
                question=market.get("question", ""),
            )
        )


def _parse_sizes(raw: str) -> list[float]:
    values: list[float] = []
    for part in raw.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        try:
            value = float(stripped)
        except ValueError:
            continue
        if value > 0:
            values.append(value)
    return values or [100.0]


def main() -> None:
    """Module CLI entrypoint."""
    app()

