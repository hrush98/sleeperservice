"""M2 checks for date cascades and partition coherence."""

from __future__ import annotations

import re

from coherence.models import (
    ComplementFinding,
    DateCascade,
    FamilyAssignment,
    FamilyType,
    ImplicationFinding,
    PairStatus,
    PartitionFinding,
    PartitionStatus,
    StrategyType,
    Violation,
)
from shared.config import settings

RANGE_THRESHOLD_PATTERN = re.compile(
    r"(?:\bover\b|\bunder\b|\babove\b|\bbelow\b|\bat least\b|\bat most\b|<=|>=|<|>)",
    flags=re.IGNORECASE,
)
RANGE_BUCKET_PATTERN = re.compile(r"(?:\bbetween\b.+\b(?:and|-)\b.+|\b\d+\s*-\s*\d+\b)", flags=re.IGNORECASE)
ON_RESIDUAL_PATTERN = re.compile(r"(?:\bnot by\b|\bnone of\b|\bafter\b|\bother\b)", flags=re.IGNORECASE)
NEGATION_TOKEN_PATTERN = re.compile(
    r"\b(?:not|no|never|without|fails?|fails to|didn t|doesn t|isn t|aren t|won t|cannot|can t)\b",
    flags=re.IGNORECASE,
)
OR_SUPERSET_PATTERN = re.compile(r"\b(?:or|either|any of)\b", flags=re.IGNORECASE)
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "will",
    "be",
    "the",
    "a",
    "an",
    "by",
    "before",
    "on",
    "in",
    "during",
    "happen",
    "happens",
    "occur",
    "occurs",
    "market",
    "this",
    "that",
    "of",
    "to",
    "for",
    "and",
}


def find_monotonicity_violations(cascades: list[DateCascade]) -> list[Violation]:
    """Return ranked violations where earlier Yes exceeds later Yes."""
    violations: list[Violation] = []
    for cascade in cascades:
        markets = cascade.markets
        for i, short_market in enumerate(markets):
            for long_market in markets[i + 1 :]:
                if short_market.yes_price <= long_market.yes_price:
                    continue
                pair_cost = long_market.yes_price + short_market.no_price
                edge_cents = (1.0 - pair_cost) * 100.0
                violations.append(
                    Violation(
                        cascade_slug=cascade.event_slug,
                        cascade_title=cascade.event_title,
                        short_market=short_market,
                        long_market=long_market,
                        pair_cost=pair_cost,
                        edge_cents=edge_cents,
                        guaranteed=True,
                    )
                )
    violations.sort(key=lambda item: item.edge_cents, reverse=True)
    return violations


def find_partition_sum_findings(
    assignments: list[FamilyAssignment],
    tolerance: float | None = None,
) -> list[PartitionFinding]:
    """Check ON/RANGE assignments with strict validity gating."""
    threshold = settings.coherence_partition_sum_tolerance if tolerance is None else tolerance
    findings: list[PartitionFinding] = []
    for assignment in assignments:
        if assignment.family not in {FamilyType.ON_PARTITION, FamilyType.RANGE_PARTITION}:
            continue
        status, reason = validate_partition_group(assignment)
        if status != PartitionStatus.VALID_PARTITION:
            findings.append(
                PartitionFinding(
                    event_slug=assignment.event_slug,
                    event_title=assignment.event_title,
                    family=assignment.family,
                    strategy=StrategyType.S_PARTITION_SUM,
                    status=status,
                    reason=reason,
                    market_count=len(assignment.markets),
                    sum_yes=0.0,
                    deviation=0.0,
                    tolerance=threshold,
                    is_violation=False,
                )
            )
            continue
        sum_yes = sum(market.yes_price for market in assignment.markets)
        deviation = abs(1.0 - sum_yes)
        findings.append(
            PartitionFinding(
                event_slug=assignment.event_slug,
                event_title=assignment.event_title,
                family=assignment.family,
                strategy=StrategyType.S_PARTITION_SUM,
                status=PartitionStatus.VALID_PARTITION,
                reason="Partition validity gate passed.",
                market_count=len(assignment.markets),
                sum_yes=sum_yes,
                deviation=deviation,
                tolerance=threshold,
                is_violation=deviation > threshold,
            )
        )
    findings.sort(key=lambda item: item.deviation, reverse=True)
    return findings


def validate_partition_group(assignment: FamilyAssignment) -> tuple[PartitionStatus, str]:
    """Return strict validity status for ON/RANGE partition groups."""
    if len(assignment.markets) < settings.coherence_partition_validity_min_bucket_count:
        return PartitionStatus.INSUFFICIENT_EVIDENCE, "Too few buckets for partition validity."

    if assignment.family == FamilyType.ON_PARTITION:
        return _validate_on_partition(assignment)
    if assignment.family == FamilyType.RANGE_PARTITION:
        return _validate_range_partition(assignment)
    return PartitionStatus.NON_APPLICABLE, "Family is not partition-scored."


def _validate_on_partition(assignment: FamilyAssignment) -> tuple[PartitionStatus, str]:
    on_count = sum(1 for market in assignment.markets if re.search(r"\bon\b", market.question, flags=re.IGNORECASE))
    residual_count = sum(1 for market in assignment.markets if ON_RESIDUAL_PATTERN.search(market.question))
    if on_count < 2:
        return PartitionStatus.NON_APPLICABLE, "ON partition lacks multiple on-date buckets."
    if residual_count == 0:
        return PartitionStatus.INSUFFICIENT_EVIDENCE, "No residual bucket detected for ON partition."
    return PartitionStatus.VALID_PARTITION, "ON partition has date buckets and residual bucket."


def _validate_range_partition(assignment: FamilyAssignment) -> tuple[PartitionStatus, str]:
    threshold_count = sum(1 for market in assignment.markets if RANGE_THRESHOLD_PATTERN.search(market.question))
    bucket_count = sum(1 for market in assignment.markets if RANGE_BUCKET_PATTERN.search(market.question))
    if threshold_count > 0 and bucket_count == 0:
        return PartitionStatus.NON_APPLICABLE, "Threshold ladder detected (not mutually exclusive buckets)."
    if bucket_count < 2:
        return PartitionStatus.INSUFFICIENT_EVIDENCE, "Range partition lacks explicit bucket structure."
    return PartitionStatus.VALID_PARTITION, "Range partition has bucketized structure."


def find_complement_findings(
    assignments: list[FamilyAssignment],
    tolerance: float | None = None,
) -> list[ComplementFinding]:
    """Check complement-style pairs for P(A)+P(not A) ~= 1."""
    threshold = settings.coherence_complement_sum_tolerance if tolerance is None else tolerance
    findings: list[ComplementFinding] = []
    for assignment in assignments:
        if assignment.family != FamilyType.BINARY_COMPLEMENT_PAIR:
            continue
        status, reason = validate_complement_pair(assignment)
        first = assignment.markets[0]
        second = assignment.markets[1] if len(assignment.markets) > 1 else assignment.markets[0]
        if status != PairStatus.VALID_PAIR:
            findings.append(
                ComplementFinding(
                    event_slug=assignment.event_slug,
                    event_title=assignment.event_title,
                    family=assignment.family,
                    strategy=StrategyType.S_COMPLEMENT,
                    status=status,
                    reason=reason,
                    market_a=first,
                    market_b=second,
                    sum_yes=0.0,
                    deviation=0.0,
                    tolerance=threshold,
                    is_violation=False,
                )
            )
            continue
        sum_yes = first.yes_price + second.yes_price
        deviation = abs(1.0 - sum_yes)
        findings.append(
            ComplementFinding(
                event_slug=assignment.event_slug,
                event_title=assignment.event_title,
                family=assignment.family,
                strategy=StrategyType.S_COMPLEMENT,
                status=PairStatus.VALID_PAIR,
                reason="Complement pair validity gate passed.",
                market_a=first,
                market_b=second,
                sum_yes=sum_yes,
                deviation=deviation,
                tolerance=threshold,
                is_violation=deviation > threshold,
            )
        )
    findings.sort(key=lambda item: item.deviation, reverse=True)
    return findings


def find_implication_findings(
    assignments: list[FamilyAssignment],
    tolerance: float | None = None,
) -> list[ImplicationFinding]:
    """Check implication pairs for bound P(A) <= P(B)."""
    threshold = settings.coherence_implication_gap_tolerance if tolerance is None else tolerance
    findings: list[ImplicationFinding] = []
    for assignment in assignments:
        if assignment.family != FamilyType.IMPLICATION_PAIR:
            continue
        status, reason = validate_implication_pair(assignment)
        antecedent = assignment.markets[0]
        consequent = assignment.markets[1] if len(assignment.markets) > 1 else assignment.markets[0]
        if status != PairStatus.VALID_PAIR:
            findings.append(
                ImplicationFinding(
                    event_slug=assignment.event_slug,
                    event_title=assignment.event_title,
                    family=assignment.family,
                    strategy=StrategyType.S2_IMPLICATION,
                    status=status,
                    reason=reason,
                    antecedent_market=antecedent,
                    consequent_market=consequent,
                    gap=0.0,
                    tolerance=threshold,
                    is_violation=False,
                )
            )
            continue
        gap = antecedent.yes_price - consequent.yes_price
        findings.append(
            ImplicationFinding(
                event_slug=assignment.event_slug,
                event_title=assignment.event_title,
                family=assignment.family,
                strategy=StrategyType.S2_IMPLICATION,
                status=PairStatus.VALID_PAIR,
                reason="Implication validity gate passed.",
                antecedent_market=antecedent,
                consequent_market=consequent,
                gap=gap,
                tolerance=threshold,
                is_violation=gap > threshold,
            )
        )
    findings.sort(key=lambda item: item.gap, reverse=True)
    return findings


def validate_complement_pair(assignment: FamilyAssignment) -> tuple[PairStatus, str]:
    """Validate complement pair structure before scoring."""
    if len(assignment.markets) != 2:
        return PairStatus.INSUFFICIENT_EVIDENCE, "Complement scoring needs exactly 2 markets."
    first, second = assignment.markets
    first_text = first.question.lower()
    second_text = second.question.lower()
    first_neg = bool(NEGATION_TOKEN_PATTERN.search(first_text))
    second_neg = bool(NEGATION_TOKEN_PATTERN.search(second_text))
    if first_neg == second_neg:
        return PairStatus.NON_APPLICABLE, "Pair lacks opposite negation cues."
    overlap = _token_overlap_ratio(_strip_negation(first_text), _strip_negation(second_text))
    if overlap < settings.coherence_complement_min_token_overlap:
        return PairStatus.INSUFFICIENT_EVIDENCE, "Complement lexical overlap is below threshold."
    return PairStatus.VALID_PAIR, "Complement pair has opposite negation and shared subject."


def validate_implication_pair(assignment: FamilyAssignment) -> tuple[PairStatus, str]:
    """Validate implication orientation before scoring."""
    if len(assignment.markets) != 2:
        return PairStatus.INSUFFICIENT_EVIDENCE, "Implication scoring needs exactly 2 markets."
    antecedent, consequent = assignment.markets
    consequent_text = consequent.question.lower()
    if not OR_SUPERSET_PATTERN.search(consequent_text):
        return PairStatus.NON_APPLICABLE, "Consequent lacks explicit union cue (or/either/any of)."
    overlap = _subset_overlap_ratio(antecedent.question.lower(), consequent_text)
    if overlap < settings.coherence_implication_min_token_overlap:
        return PairStatus.INSUFFICIENT_EVIDENCE, "Implication lexical subset overlap is below threshold."
    return PairStatus.VALID_PAIR, "Implication pair has directed subset semantics."


def _strip_negation(text: str) -> str:
    stripped = NEGATION_TOKEN_PATTERN.sub(" ", text)
    return " ".join(stripped.split())


def _content_tokens(text: str) -> set[str]:
    return {token for token in TOKEN_PATTERN.findall(text.lower()) if token not in STOPWORDS}


def _token_overlap_ratio(first: str, second: str) -> float:
    first_tokens = _content_tokens(first)
    second_tokens = _content_tokens(second)
    if not first_tokens or not second_tokens:
        return 0.0
    overlap = len(first_tokens.intersection(second_tokens))
    return overlap / min(len(first_tokens), len(second_tokens))


def _subset_overlap_ratio(subset_text: str, superset_text: str) -> float:
    subset_tokens = _content_tokens(subset_text)
    superset_tokens = _content_tokens(superset_text)
    if not subset_tokens:
        return 0.0
    overlap = len(subset_tokens.intersection(superset_tokens))
    return overlap / len(subset_tokens)

