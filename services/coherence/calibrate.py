"""Interactive experiment script to calibrate semantic fallback thresholds."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import typer
from sentence_transformers import SentenceTransformer

from services.coherence.detector import parse_market_date
from services.coherence.models import CandidateEvent, MarketInfo
from services.coherence.scanner import scan_market_catalog
from services.shared.config import settings

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="coherence-calibrate",
    help="Calibrate semantic fallback thresholds for date-cascade detection.",
    add_completion=False,
)


@dataclass(slots=True)
class CandidateEval:
    """Precomputed features for one candidate event."""

    event_id: str
    event_slug: str
    event_title: str
    markets: list[MarketInfo]
    stem_match: bool
    description_consistent: bool
    distinct_date_count: int
    min_semantic_similarity: float | None


@dataclass(slots=True)
class ThresholdStats:
    """Counts for one tested semantic threshold."""

    threshold: float
    accepted_total: int
    strict_accepted: int
    fallback_accepted: int
    rejected: int
    rejected_by_dates: int
    rejected_by_descriptions: int


@app.command()
def run(
    thresholds: str = typer.Option(
        "0.83,0.88,0.90,0.92",
        "--thresholds",
        help="Comma-separated semantic thresholds to evaluate.",
    ),
    limit: int = typer.Option(100, "--limit", help="Gamma page size per request."),
    sample_size: int = typer.Option(
        12,
        "--sample-size",
        help="Fallback candidates to manually label per threshold (0 to skip prompts).",
    ),
    min_distinct_dates: int = typer.Option(
        2,
        "--min-distinct-dates",
        help="Require at least this many distinct parsed dates.",
    ),
    require_description_consistency: bool = typer.Option(
        True,
        "--require-description-consistency/--allow-description-inconsistent",
        help="Require description consistency for semantic-fallback acceptance.",
    ),
    seed: int = typer.Option(7, "--seed", help="Random seed for sampling."),
    output_path: str = typer.Option(
        "services/coherence/calibration_results.json",
        "--output-path",
        help="Path to write calibration report JSON.",
    ),
) -> None:
    """Run calibration and optionally prompt for manual labels."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    parsed_thresholds = _parse_thresholds(thresholds)
    if not parsed_thresholds:
        raise typer.BadParameter("No valid thresholds parsed from --thresholds.")

    typer.echo("Fetching catalog and candidate events...")
    _events, candidates = scan_market_catalog(limit=limit)
    typer.echo(f"Candidate events loaded: {len(candidates)}")

    typer.echo(f"Loading semantic model: {settings.coherence_semantic_model_name}")
    model = SentenceTransformer(settings.coherence_semantic_model_name)
    candidate_evals = _build_candidate_evals(candidates, model)
    typer.echo(f"Precomputed candidate features: {len(candidate_evals)}")

    stats_by_threshold: list[ThresholdStats] = []
    labels_by_threshold: dict[str, dict[str, int]] = {}
    randomizer = random.Random(seed)

    for threshold in parsed_thresholds:
        stats, fallback_pool = _evaluate_threshold(
            candidate_evals=candidate_evals,
            threshold=threshold,
            min_distinct_dates=min_distinct_dates,
            require_description_consistency=require_description_consistency,
        )
        stats_by_threshold.append(stats)

        typer.echo("")
        typer.echo(_format_stats(stats))
        if sample_size <= 0:
            continue

        typer.echo(
            f"Manual labeling for threshold={threshold:.2f} "
            f"(pool size={len(fallback_pool)}, sample={min(sample_size, len(fallback_pool))})"
        )
        labels = _prompt_manual_labels(
            fallback_pool=fallback_pool,
            threshold=threshold,
            sample_size=sample_size,
            randomizer=randomizer,
        )
        labels_by_threshold[f"{threshold:.2f}"] = labels

    recommendation = _recommend_threshold(stats_by_threshold, labels_by_threshold)
    typer.echo("")
    typer.echo("Recommendation:")
    typer.echo(recommendation)

    _write_report(
        output_path=output_path,
        thresholds=parsed_thresholds,
        stats_by_threshold=stats_by_threshold,
        labels_by_threshold=labels_by_threshold,
        recommendation=recommendation,
        min_distinct_dates=min_distinct_dates,
        require_description_consistency=require_description_consistency,
        sample_size=sample_size,
    )
    typer.echo(f"Wrote calibration report: {_resolve_repo_relative_path(output_path)}")


def _build_candidate_evals(
    candidates: list[CandidateEvent],
    model: SentenceTransformer,
) -> list[CandidateEval]:
    evals: list[CandidateEval] = []
    for candidate in candidates:
        markets = _with_parsed_dates(candidate.markets)
        if len(markets) < 2:
            continue
        if any(market.parsed_date is None for market in markets):
            # Treat as non-actionable for calibration.
            continue
        markets.sort(key=lambda market: market.parsed_date or date.max)

        stem_values = {_normalized_stem(market.question) for market in markets}
        stem_match = len(stem_values) == 1
        descriptions_consistent = _descriptions_consistent(markets)
        distinct_date_count = len({market.parsed_date for market in markets if market.parsed_date is not None})

        semantic_score: float | None = None
        if not stem_match:
            texts = [_semantic_text_for_market(market) for market in markets]
            vectors = model.encode(texts, normalize_embeddings=True)
            semantic_score = _min_pairwise_cosine(vectors)

        evals.append(
            CandidateEval(
                event_id=candidate.event_id,
                event_slug=candidate.event_slug,
                event_title=candidate.event_title,
                markets=markets,
                stem_match=stem_match,
                description_consistent=descriptions_consistent,
                distinct_date_count=distinct_date_count,
                min_semantic_similarity=semantic_score,
            )
        )
    return evals


def _evaluate_threshold(
    candidate_evals: list[CandidateEval],
    threshold: float,
    min_distinct_dates: int,
    require_description_consistency: bool,
) -> tuple[ThresholdStats, list[CandidateEval]]:
    accepted_total = 0
    strict_accepted = 0
    fallback_accepted = 0
    rejected = 0
    rejected_by_dates = 0
    rejected_by_descriptions = 0
    fallback_pool: list[CandidateEval] = []

    for candidate in candidate_evals:
        if candidate.distinct_date_count < min_distinct_dates:
            rejected += 1
            rejected_by_dates += 1
            continue
        if require_description_consistency and not candidate.description_consistent:
            rejected += 1
            rejected_by_descriptions += 1
            continue
        if candidate.stem_match:
            accepted_total += 1
            strict_accepted += 1
            continue
        if (
            candidate.min_semantic_similarity is not None
            and candidate.min_semantic_similarity >= threshold
        ):
            accepted_total += 1
            fallback_accepted += 1
            fallback_pool.append(candidate)
        else:
            rejected += 1

    return (
        ThresholdStats(
            threshold=threshold,
            accepted_total=accepted_total,
            strict_accepted=strict_accepted,
            fallback_accepted=fallback_accepted,
            rejected=rejected,
            rejected_by_dates=rejected_by_dates,
            rejected_by_descriptions=rejected_by_descriptions,
        ),
        fallback_pool,
    )


def _prompt_manual_labels(
    fallback_pool: list[CandidateEval],
    threshold: float,
    sample_size: int,
    randomizer: random.Random,
) -> dict[str, int]:
    labels = {"true": 0, "false": 0, "skip": 0}
    if not fallback_pool:
        return labels

    sample = list(fallback_pool)
    randomizer.shuffle(sample)
    sample = sample[: min(sample_size, len(sample))]

    typer.echo("Label each sampled fallback candidate: [t]rue [f]alse [s]kip [q]quit")
    for idx, candidate in enumerate(sample, start=1):
        typer.echo("")
        typer.echo(f"[{idx}/{len(sample)}] threshold={threshold:.2f}")
        typer.echo(
            f"Event: {candidate.event_title} ({candidate.event_slug}) | "
            f"min_similarity={candidate.min_semantic_similarity:.4f} | "
            f"distinct_dates={candidate.distinct_date_count} | "
            f"descriptions_consistent={candidate.description_consistent}"
        )
        for market in candidate.markets:
            parsed = market.parsed_date.isoformat() if market.parsed_date else "NA"
            typer.echo(f"  - {parsed} | {market.question}")

        choice = typer.prompt("Label").strip().lower()
        if choice == "q":
            typer.echo("Stopped manual labeling early.")
            break
        if choice == "t":
            labels["true"] += 1
        elif choice == "f":
            labels["false"] += 1
        else:
            labels["skip"] += 1
    return labels


def _recommend_threshold(
    stats_by_threshold: list[ThresholdStats],
    labels_by_threshold: dict[str, dict[str, int]],
) -> str:
    best_threshold: float | None = None
    best_precision = -1.0

    for stats in stats_by_threshold:
        key = f"{stats.threshold:.2f}"
        labels = labels_by_threshold.get(key, {})
        true_count = labels.get("true", 0)
        false_count = labels.get("false", 0)
        denom = true_count + false_count
        if denom == 0:
            continue
        precision = true_count / denom
        if precision > best_precision:
            best_precision = precision
            best_threshold = stats.threshold

    if best_threshold is None:
        return (
            "No manual true/false labels were provided. "
            "Use a conservative threshold (>=0.90) and re-run with labeling enabled."
        )
    return (
        f"Best observed manual precision at threshold={best_threshold:.2f} "
        f"(precision={best_precision:.2%})."
    )


def _write_report(
    output_path: str,
    thresholds: list[float],
    stats_by_threshold: list[ThresholdStats],
    labels_by_threshold: dict[str, dict[str, int]],
    recommendation: str,
    min_distinct_dates: int,
    require_description_consistency: bool,
    sample_size: int,
) -> None:
    path = _resolve_repo_relative_path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "thresholds": thresholds,
        "min_distinct_dates": min_distinct_dates,
        "require_description_consistency": require_description_consistency,
        "sample_size": sample_size,
        "stats_by_threshold": [asdict(stats) for stats in stats_by_threshold],
        "labels_by_threshold": labels_by_threshold,
        "recommendation": recommendation,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _resolve_repo_relative_path(path_text: str) -> Path:
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / path


def _parse_thresholds(raw: str) -> list[float]:
    values: list[float] = []
    for part in raw.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        try:
            value = float(stripped)
        except ValueError:
            continue
        if 0.0 <= value <= 1.0:
            values.append(value)
    return sorted(set(values))


def _format_stats(stats: ThresholdStats) -> str:
    return (
        f"threshold={stats.threshold:.2f} | accepted={stats.accepted_total} "
        f"(strict={stats.strict_accepted}, fallback={stats.fallback_accepted}) "
        f"| rejected={stats.rejected} "
        f"[dates={stats.rejected_by_dates}, descriptions={stats.rejected_by_descriptions}]"
    )


def _with_parsed_dates(markets: list[MarketInfo]) -> list[MarketInfo]:
    return [
        MarketInfo(
            market_id=market.market_id,
            question=market.question,
            description=market.description,
            yes_price=market.yes_price,
            no_price=market.no_price,
            end_date=market.end_date,
            event_slug=market.event_slug,
            event_id=market.event_id,
            volume=market.volume,
            liquidity=market.liquidity,
            clob_token_ids=market.clob_token_ids,
            parsed_date=parse_market_date(market.question, market.end_date),
        )
        for market in markets
    ]


def _normalized_stem(question: str) -> str:
    lowered = question.lower().strip()
    without_date = _DATE_FRAGMENT.sub("", lowered)
    without_punctuation = _NON_WORD.sub(" ", without_date)
    return " ".join(without_punctuation.split())


def _descriptions_consistent(markets: list[MarketInfo]) -> bool:
    descriptions = [market.description.strip() for market in markets if market.description.strip()]
    if len(descriptions) < 2:
        return True
    anchor = descriptions[0]
    for candidate in descriptions[1:]:
        ratio = _sequence_similarity(anchor, candidate)
        if ratio < settings.coherence_description_threshold:
            return False
    return True


def _sequence_similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, left, right).ratio()


def _semantic_text_for_market(market: MarketInfo) -> str:
    question_without_date = _DATE_FRAGMENT.sub("", market.question).strip()
    if market.description:
        return f"{question_without_date}\n{market.description.strip()}"
    return question_without_date


def _min_pairwise_cosine(vectors: Any) -> float:
    min_similarity = 1.0
    for idx in range(len(vectors)):
        left = vectors[idx]
        for jdx in range(idx + 1, len(vectors)):
            right = vectors[jdx]
            similarity = float(sum(lv * rv for lv, rv in zip(left, right)))
            min_similarity = min(min_similarity, similarity)
    return min_similarity


import re as _re

_MONTH_PATTERN = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DATE_FRAGMENT = _re.compile(
    rf"\b(?:by|before|on|in|during)\s+(?:{_MONTH_PATTERN}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?|{_MONTH_PATTERN}\s+\d{{4}}|\d{{4}})\b",
    flags=_re.IGNORECASE,
)
_NON_WORD = _re.compile(r"[^\w\s]")


def main() -> None:
    """Module entrypoint."""
    app()


if __name__ == "__main__":
    main()
