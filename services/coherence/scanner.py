"""M0 scanner: pull active events, normalize candidates, assign families."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.coherence.models import (
    CandidateEvent,
    FamilyAssignment,
    FamilyType,
    MarketInfo,
    StrategyCandidate,
    StrategyType,
)
from services.shared.config import settings
from services.shared.polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)
_semantic_model: Any | None = None
_semantic_model_failed = False
BY_HINT_PATTERN = re.compile(r"\b(?:by|before)\b", flags=re.IGNORECASE)
ON_HINT_PATTERN = re.compile(r"\bon\b", flags=re.IGNORECASE)
RANGE_HINT_PATTERN = re.compile(
    r"(?:\bbetween\b|\bunder\b|\bover\b|\bless than\b|\bmore than\b|\bat least\b|\bat most\b|<=|>=|<|>|\b\d+\s*-\s*\d+\b)",
    flags=re.IGNORECASE,
)
DATE_FRAGMENT_PATTERN = re.compile(
    r"\b(?:by|before|on|in|during)\s+"
    r"(?:[a-z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|[a-z]+\s+\d{4}|\d{4})\b",
    flags=re.IGNORECASE,
)
RANGE_FRAGMENT_PATTERN = re.compile(
    r"(?:\bbetween\b.+?\b(?:and|-)\b.+|<=|>=|<|>|\bunder\b.+|\bover\b.+|\babove\b.+|\bbelow\b.+|\bat least\b.+|\bat most\b.+)",
    flags=re.IGNORECASE,
)
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


def scan_market_catalog(
    client: PolymarketClient | None = None,
    limit: int = 100,
) -> tuple[list[dict[str, Any]], list[CandidateEvent]]:
    """Return all active/unclosed events and filtered date-cascade candidates."""
    owned_client = client is None
    client = client or PolymarketClient()
    try:
        events = _fetch_all_active_events(client=client, limit=limit)
        candidates = _filter_candidate_events(events)
        return events, candidates
    finally:
        if owned_client:
            client.close()


def write_catalog_cache(
    events: list[dict[str, Any]],
    candidates: list[CandidateEvent],
    cache_path: str | None = None,
) -> Path:
    """Persist raw event payloads and normalized candidates to JSON."""
    destination = Path(cache_path or settings.coherence_cache_path).expanduser()
    if not destination.is_absolute():
        repo_root = Path(__file__).resolve().parents[2]
        destination = repo_root / destination
    destination.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(events),
        "candidate_count": len(candidates),
        "events": events,
        "candidate_events": [candidate.to_dict() for candidate in candidates],
    }
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("Wrote coherence cache: %s", destination)
    return destination


def assign_families(candidates: list[CandidateEvent]) -> list[FamilyAssignment]:
    """Assign structural families per event subgroup."""
    assignments: list[FamilyAssignment] = []
    for candidate in candidates:
        assignments.extend(_decompose_and_assign(candidate))
    return assignments


def route_strategy_candidates(assignments: list[FamilyAssignment]) -> list[StrategyCandidate]:
    """Route each family assignment into one strategy lane."""
    routed: list[StrategyCandidate] = []
    for assignment in assignments:
        if assignment.family == FamilyType.BY_CASCADE:
            routed.append(
                StrategyCandidate(
                    assignment=assignment,
                    strategy=StrategyType.S1_MONOTONICITY,
                    ready=True,
                    reason="Date-cascade candidate routed to monotonicity checks.",
                )
            )
            continue
        if assignment.family in {FamilyType.ON_PARTITION, FamilyType.RANGE_PARTITION}:
            routed.append(
                StrategyCandidate(
                    assignment=assignment,
                    strategy=StrategyType.S_PARTITION_SUM,
                    ready=True,
                    reason="Partition-style candidate routed to sum-coherence checks.",
                )
            )
            continue
        if assignment.family == FamilyType.BINARY_COMPLEMENT_PAIR:
            routed.append(
                StrategyCandidate(
                    assignment=assignment,
                    strategy=StrategyType.S_COMPLEMENT,
                    ready=True,
                    reason="Complement pair routed to sum-consistency checks.",
                )
            )
            continue
        if assignment.family == FamilyType.IMPLICATION_PAIR:
            routed.append(
                StrategyCandidate(
                    assignment=assignment,
                    strategy=StrategyType.S2_IMPLICATION,
                    ready=True,
                    reason="Implication candidate routed to P(A)<=P(B) checks.",
                )
            )
            continue
        strategy = StrategyType.S_STUB
        if assignment.family == FamilyType.JOINT_TRIPLE:
            strategy = StrategyType.S3_FRECHET
        routed.append(
            StrategyCandidate(
                assignment=assignment,
                strategy=strategy,
                ready=False,
                reason="Family recognized but not enabled in this phase.",
            )
        )
    return routed


def family_counts(assignments: list[FamilyAssignment]) -> dict[str, int]:
    """Aggregate assignment counts by family label."""
    counts: dict[str, int] = {}
    for assignment in assignments:
        key = assignment.family.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def strategy_counts(candidates: list[StrategyCandidate]) -> dict[str, int]:
    """Aggregate routed strategy counts with readiness split."""
    counts: dict[str, int] = {}
    for candidate in candidates:
        suffix = "ready" if candidate.ready else "stub"
        key = f"{candidate.strategy.value}:{suffix}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _fetch_all_active_events(client: PolymarketClient, limit: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = client._gamma_request(
            "/events",
            {
                "active": "true",
                "closed": "false",
                "limit": limit,
                "offset": offset,
            },
        )
        normalized_batch = _normalize_batch(batch)
        if not normalized_batch:
            break
        events.extend(normalized_batch)
        if len(normalized_batch) < limit:
            break
        offset += limit
    logger.info("Fetched %d active events", len(events))
    return events


def _normalize_batch(batch: Any) -> list[dict[str, Any]]:
    if isinstance(batch, list):
        return [item for item in batch if isinstance(item, dict)]
    if isinstance(batch, dict):
        if isinstance(batch.get("data"), list):
            return [item for item in batch["data"] if isinstance(item, dict)]
        if isinstance(batch.get("events"), list):
            return [item for item in batch["events"] if isinstance(item, dict)]
    return []


def _filter_candidate_events(events: list[dict[str, Any]]) -> list[CandidateEvent]:
    candidates: list[CandidateEvent] = []
    for event in events:
        event_markets = event.get("markets")
        if not isinstance(event_markets, list):
            continue
        normalized_markets = [
            market
            for market in (_normalize_market(raw_market, event) for raw_market in event_markets)
            if market is not None
        ]
        if len(normalized_markets) < 2:
            continue
        candidates.append(
            CandidateEvent(
                event_id=str(event.get("id", "")),
                event_slug=str(event.get("slug", "")),
                event_title=str(event.get("title", "")),
                markets=normalized_markets,
            )
        )
    logger.info("Filtered %d candidate events", len(candidates))
    return candidates


def _classify_family(markets: list[MarketInfo]) -> tuple[FamilyType, float, str]:
    if len(markets) < settings.coherence_family_min_partition_markets:
        return FamilyType.MIXED_HYBRID, 0.3, "Too few markets for stable partition/cascade detection."

    by_hits = sum(1 for market in markets if BY_HINT_PATTERN.search(market.question))
    on_hits = sum(1 for market in markets if ON_HINT_PATTERN.search(market.question))
    range_hits = sum(1 for market in markets if RANGE_HINT_PATTERN.search(market.question))
    total = len(markets)

    if by_hits == total and on_hits == 0:
        return FamilyType.BY_CASCADE, 0.9, "All questions look like by/before structure."
    if on_hits == total and range_hits == 0:
        return FamilyType.ON_PARTITION, 0.8, "All questions look like on-date buckets."
    if range_hits == total:
        return FamilyType.RANGE_PARTITION, 0.8, "All questions look like value-range buckets."
    if by_hits > 0 and on_hits > 0:
        return FamilyType.MIXED_HYBRID, 0.5, "Mixed by/on language indicates a hybrid event."
    if range_hits > 0 and (by_hits > 0 or on_hits > 0):
        return FamilyType.MIXED_HYBRID, 0.5, "Mixed range and temporal language indicates a hybrid event."
    return FamilyType.MIXED_HYBRID, 0.4, "Unclear structure, routed to hybrid stub."


def _decompose_and_assign(candidate: CandidateEvent) -> list[FamilyAssignment]:
    rule_groups, leftovers = _rule_first_groups(candidate.markets)
    groups: list[tuple[str, list[MarketInfo], bool]] = []
    for key, markets in rule_groups.items():
        if len(markets) >= settings.coherence_decomposition_min_subgroup_markets:
            groups.append((key, markets, False))
        else:
            leftovers.extend(markets)
    semantic_groups = _semantic_group_leftovers(leftovers)
    groups.extend(semantic_groups)
    if not semantic_groups and len(leftovers) >= settings.coherence_decomposition_min_subgroup_markets:
        groups.append(("leftovers:fallback", leftovers, False))

    assignments: list[FamilyAssignment] = []
    for index, (group_key, markets, used_semantic) in enumerate(groups, start=1):
        special = _classify_special_pair(markets)
        if special is not None:
            family, confidence, notes, ordered_markets = special
        else:
            family, confidence, notes = _classify_family(markets)
            ordered_markets = markets
        subgroup_id = f"{candidate.event_id}:g{index}"
        assignments.append(
            FamilyAssignment(
                event_id=subgroup_id,
                event_slug=candidate.event_slug,
                event_title=candidate.event_title,
                parent_event_id=candidate.event_id,
                group_id=subgroup_id,
                group_key=group_key,
                family=family,
                markets=ordered_markets,
                confidence=confidence,
                semantic_fallback_used=used_semantic,
                notes=notes,
            )
        )
    return assignments


def _classify_special_pair(
    markets: list[MarketInfo],
) -> tuple[FamilyType, float, str, list[MarketInfo]] | None:
    if len(markets) != 2:
        return None
    first, second = markets
    if _is_complement_pair(first, second):
        return (
            FamilyType.BINARY_COMPLEMENT_PAIR,
            0.9,
            "Detected complementary phrasing with opposite negation cues.",
            [first, second],
        )
    implication = _implication_pair(first, second)
    if implication is None:
        return None
    antecedent, consequent = implication
    notes = (
        "Detected implication-style pair via conservative lexical subset "
        f"(antecedent={antecedent.market_id}, consequent={consequent.market_id})."
    )
    return FamilyType.IMPLICATION_PAIR, 0.78, notes, [antecedent, consequent]


def _is_complement_pair(first: MarketInfo, second: MarketInfo) -> bool:
    first_text = _normalized_group_stem(first.question)
    second_text = _normalized_group_stem(second.question)
    if not first_text or not second_text:
        return False
    first_neg = bool(NEGATION_TOKEN_PATTERN.search(first_text))
    second_neg = bool(NEGATION_TOKEN_PATTERN.search(second_text))
    if first_neg == second_neg:
        return False
    first_stripped = _strip_negation_tokens(first_text)
    second_stripped = _strip_negation_tokens(second_text)
    if not first_stripped or not second_stripped:
        return False
    return _token_overlap_ratio(first_stripped, second_stripped) >= settings.coherence_complement_min_token_overlap


def _implication_pair(first: MarketInfo, second: MarketInfo) -> tuple[MarketInfo, MarketInfo] | None:
    first_text = _normalized_group_stem(first.question)
    second_text = _normalized_group_stem(second.question)
    if not first_text or not second_text:
        return None
    first_has_union = bool(OR_SUPERSET_PATTERN.search(first_text))
    second_has_union = bool(OR_SUPERSET_PATTERN.search(second_text))
    if first_has_union == second_has_union:
        return None

    if second_has_union and _is_implication_direction(first_text, second_text):
        return first, second
    if first_has_union and _is_implication_direction(second_text, first_text):
        return second, first
    return None


def _is_implication_direction(antecedent_text: str, consequent_text: str) -> bool:
    antecedent_tokens = _content_tokens(antecedent_text)
    consequent_tokens = _content_tokens(consequent_text)
    if not antecedent_tokens or not consequent_tokens:
        return False
    overlap = len(antecedent_tokens.intersection(consequent_tokens))
    overlap_ratio = overlap / len(antecedent_tokens)
    if overlap_ratio < settings.coherence_implication_min_token_overlap:
        return False
    return len(consequent_tokens) >= len(antecedent_tokens) + 1


def _strip_negation_tokens(text: str) -> str:
    stripped = NEGATION_TOKEN_PATTERN.sub(" ", text)
    return " ".join(stripped.split())


def _token_overlap_ratio(first_text: str, second_text: str) -> float:
    first_tokens = _content_tokens(first_text)
    second_tokens = _content_tokens(second_text)
    if not first_tokens or not second_tokens:
        return 0.0
    overlap = len(first_tokens.intersection(second_tokens))
    denominator = min(len(first_tokens), len(second_tokens))
    return overlap / denominator if denominator else 0.0


def _content_tokens(text: str) -> set[str]:
    return {token for token in TOKEN_PATTERN.findall(text.lower()) if token not in STOPWORDS}


def _rule_first_groups(markets: list[MarketInfo]) -> tuple[dict[str, list[MarketInfo]], list[MarketInfo]]:
    grouped: dict[str, list[MarketInfo]] = {}
    leftovers: list[MarketInfo] = []
    for market in markets:
        cue = _cue_type(market.question)
        stem = _normalized_group_stem(market.question)
        if not stem:
            leftovers.append(market)
            continue
        key = f"{cue}:{stem}"
        grouped.setdefault(key, []).append(market)
    return grouped, leftovers


def _cue_type(question: str) -> str:
    if BY_HINT_PATTERN.search(question):
        return "by"
    if ON_HINT_PATTERN.search(question):
        return "on"
    if RANGE_HINT_PATTERN.search(question):
        return "range"
    return "other"


def _normalized_group_stem(question: str) -> str:
    lowered = question.lower().strip()
    no_date = DATE_FRAGMENT_PATTERN.sub(" ", lowered)
    no_range = RANGE_FRAGMENT_PATTERN.sub(" ", no_date)
    no_punct = re.sub(r"[^\w\s]", " ", no_range)
    return " ".join(no_punct.split())


def _semantic_group_leftovers(leftovers: list[MarketInfo]) -> list[tuple[str, list[MarketInfo], bool]]:
    if len(leftovers) < settings.coherence_decomposition_min_subgroup_markets:
        return []
    if not settings.coherence_semantic_fallback_enabled:
        return []
    model = _get_semantic_model()
    if model is None:
        return []

    texts = [f"{market.question}\n{market.description}" for market in leftovers]
    embeddings = model.encode(texts, normalize_embeddings=True)
    threshold = settings.coherence_decomposition_semantic_similarity_threshold
    groups: list[tuple[str, list[MarketInfo], bool]] = []
    used: set[int] = set()
    for idx, emb in enumerate(embeddings):
        if idx in used:
            continue
        cluster = [idx]
        used.add(idx)
        for jdx in range(idx + 1, len(embeddings)):
            if jdx in used:
                continue
            similarity = float(sum(left * right for left, right in zip(emb, embeddings[jdx])))
            if similarity >= threshold:
                cluster.append(jdx)
                used.add(jdx)
        if len(cluster) < settings.coherence_decomposition_min_subgroup_markets:
            continue
        grouped_markets = [leftovers[pos] for pos in cluster]
        groups.append((f"semantic:{idx}", grouped_markets, True))
    return groups


def decomposition_stats(assignments: list[FamilyAssignment], source_events: int) -> dict[str, int]:
    """Return decomposition counters for CLI reporting."""
    semantic_groups = sum(1 for item in assignments if item.semantic_fallback_used)
    parent_counts: dict[str, int] = {}
    for assignment in assignments:
        parent_counts[assignment.parent_event_id] = parent_counts.get(assignment.parent_event_id, 0) + 1
    hybrid_events_seen = sum(1 for count in parent_counts.values() if count > 1)
    return {
        "hybrid_events_seen": hybrid_events_seen,
        "source_events": source_events,
        "subgroups_emitted": len(assignments),
        "semantic_fallback_groups": semantic_groups,
    }


def _get_semantic_model() -> Any | None:
    global _semantic_model  # noqa: PLW0603
    global _semantic_model_failed  # noqa: PLW0603
    if _semantic_model is not None:
        return _semantic_model
    if _semantic_model_failed:
        return None
    try:
        from sentence_transformers import SentenceTransformer

        _semantic_model = SentenceTransformer(settings.coherence_semantic_model_name)
        logger.info("Loaded decomposition model: %s", settings.coherence_semantic_model_name)
        return _semantic_model
    except Exception as exc:  # pragma: no cover
        _semantic_model_failed = True
        logger.warning("Semantic decomposition unavailable: %s", exc)
        return None


def _normalize_market(raw_market: Any, event: dict[str, Any]) -> MarketInfo | None:
    if not isinstance(raw_market, dict):
        return None
    if not raw_market.get("active", True) or raw_market.get("closed", False):
        return None

    outcomes = _parse_string_or_list(raw_market.get("outcomes"))
    outcome_prices = _parse_string_or_list(raw_market.get("outcomePrices"))
    if len(outcomes) < 2 or len(outcome_prices) < 2:
        return None

    normalized_outcomes = [str(value).strip().lower() for value in outcomes]
    if set(normalized_outcomes[:2]) != {"yes", "no"}:
        return None

    yes_idx = normalized_outcomes.index("yes")
    no_idx = normalized_outcomes.index("no")
    try:
        yes_price = float(outcome_prices[yes_idx])
        no_price = float(outcome_prices[no_idx])
    except (ValueError, TypeError, IndexError):
        return None

    # Skip markets that are effectively resolved.
    if yes_price <= 0.0 or yes_price >= 1.0 or no_price <= 0.0 or no_price >= 1.0:
        return None

    volume = _to_float(raw_market.get("volume"))
    liquidity = _to_float(raw_market.get("liquidity"))
    if volume < settings.coherence_min_volume:
        return None
    if liquidity < settings.coherence_min_liquidity:
        return None

    token_ids = [str(token) for token in _parse_string_or_list(raw_market.get("clobTokenIds"))]
    yes_token_id = token_ids[yes_idx] if yes_idx < len(token_ids) else ""
    no_token_id = token_ids[no_idx] if no_idx < len(token_ids) else ""

    return MarketInfo(
        market_id=str(raw_market.get("id", "")),
        question=str(raw_market.get("question", "")),
        description=str(raw_market.get("description", "")),
        yes_price=yes_price,
        no_price=no_price,
        end_date=str(raw_market.get("endDate", "")),
        event_slug=str(event.get("slug", "")),
        event_id=str(event.get("id", "")),
        volume=volume,
        liquidity=liquidity,
        clob_token_ids=token_ids,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
    )


def _parse_string_or_list(raw_value: Any) -> list[Any]:
    if isinstance(raw_value, list):
        return raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
