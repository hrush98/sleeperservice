"""M1 detector: identify sorted date cascades from candidate events."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

from coherence.models import CandidateEvent, DateCascade, MarketInfo
from shared.config import settings

logger = logging.getLogger(__name__)
_semantic_model: Any | None = None
_semantic_model_failed = False

MONTH_PATTERN = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_WITH_YEAR = re.compile(
    rf"\b(?:by|before|on)\s+({MONTH_PATTERN}\s+\d{{1,2}}(?:st|nd|rd|th)?[,]?\s+\d{{4}})\b",
    flags=re.IGNORECASE,
)
DATE_NO_YEAR = re.compile(
    rf"\b(?:by|before|on)\s+({MONTH_PATTERN}\s+\d{{1,2}}(?:st|nd|rd|th)?)\b",
    flags=re.IGNORECASE,
)
MONTH_YEAR = re.compile(
    rf"\b(?:by|before|in|during)\s+({MONTH_PATTERN}\s+\d{{4}})\b",
    flags=re.IGNORECASE,
)
YEAR_ONLY = re.compile(r"\b(?:in|during|by)\s+(\d{4})\b", flags=re.IGNORECASE)
DATE_FRAGMENT = re.compile(
    rf"\b(?:by|before|on|in|during)\s+(?:{MONTH_PATTERN}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?|{MONTH_PATTERN}\s+\d{{4}}|\d{{4}})\b",
    flags=re.IGNORECASE,
)


def detect_date_cascades(candidates: list[CandidateEvent]) -> tuple[list[DateCascade], dict[str, int]]:
    """Build sorted DateCascade entries and diagnostics from candidates."""
    cascades: list[DateCascade] = []
    diagnostics = {
        "stem_mismatch": 0,
        "date_parse_failed": 0,
        "too_few_markets": 0,
        "semantic_fallback_used": 0,
        "semantic_model_unavailable": 0,
    }
    for candidate in candidates:
        parsed_markets = _with_parsed_dates(candidate.markets)
        if len(parsed_markets) < 2:
            diagnostics["too_few_markets"] += 1
            continue
        parsed_markets.sort(key=lambda market: (market.parsed_date or date.max))
        if any(market.parsed_date is None for market in parsed_markets):
            diagnostics["date_parse_failed"] += 1
            continue

        stems = {_normalized_stem(market.question) for market in parsed_markets}
        if len(stems) != 1:
            if _semantic_series_match(parsed_markets):
                diagnostics["semantic_fallback_used"] += 1
            else:
                if settings.coherence_semantic_fallback_enabled and _get_semantic_model() is None:
                    diagnostics["semantic_model_unavailable"] += 1
                diagnostics["stem_mismatch"] += 1
                continue
        descriptions_consistent = _descriptions_consistent(parsed_markets)
        cascades.append(
            DateCascade(
                event_slug=candidate.event_slug,
                event_title=candidate.event_title,
                markets=parsed_markets,
                descriptions_consistent=descriptions_consistent,
            )
        )
    logger.info("Detected %d date cascades", len(cascades))
    return cascades, diagnostics


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
            yes_token_id=market.yes_token_id,
            no_token_id=market.no_token_id,
            parsed_date=parse_market_date(market.question, market.end_date),
        )
        for market in markets
    ]


def parse_market_date(question: str, end_date: str | None = None) -> date | None:
    """Parse market date from question text, falling back to endDate year when needed."""
    end_dt = _parse_iso_date(end_date)

    with_year = DATE_WITH_YEAR.search(question)
    if with_year:
        parsed = _try_strptime(with_year.group(1), ("%B %d %Y", "%b %d %Y", "%B %d, %Y", "%b %d, %Y"))
        if parsed:
            return parsed

    no_year = DATE_NO_YEAR.search(question)
    if no_year:
        year = end_dt.year if end_dt else datetime.now().year
        parsed = _try_strptime(f"{no_year.group(1)} {year}", ("%B %d %Y", "%b %d %Y"))
        if parsed:
            return parsed

    month_year = MONTH_YEAR.search(question)
    if month_year:
        parsed = _try_strptime(month_year.group(1), ("%B %Y", "%b %Y"))
        if parsed:
            return parsed.replace(day=1)

    year_only = YEAR_ONLY.search(question)
    if year_only:
        return date(int(year_only.group(1)), 1, 1)

    return end_dt


def _parse_iso_date(end_date: str | None) -> date | None:
    if not end_date:
        return None
    text = end_date.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _try_strptime(raw: str, formats: tuple[str, ...]) -> date | None:
    clean = re.sub(r"(\d)(st|nd|rd|th)", r"\1", raw.lower(), flags=re.IGNORECASE)
    for fmt in formats:
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return None


def _normalized_stem(question: str) -> str:
    lowered = question.lower().strip()
    without_date = DATE_FRAGMENT.sub("", lowered)
    without_punctuation = re.sub(r"[^\w\s]", " ", without_date)
    return " ".join(without_punctuation.split())


def _descriptions_consistent(markets: list[MarketInfo]) -> bool:
    descriptions = [market.description.strip() for market in markets if market.description.strip()]
    if len(descriptions) < 2:
        return True
    anchor = descriptions[0]
    for candidate in descriptions[1:]:
        if SequenceMatcher(None, anchor, candidate).ratio() < settings.coherence_description_threshold:
            return False
    return True


def _semantic_series_match(markets: list[MarketInfo]) -> bool:
    if not settings.coherence_semantic_fallback_enabled:
        return False
    model = _get_semantic_model()
    if model is None:
        return False

    texts = [_semantic_text_for_match(market) for market in markets]
    if len(texts) < 2:
        return False
    embeddings = model.encode(texts, normalize_embeddings=True)
    min_similarity = 1.0
    for idx, left in enumerate(embeddings):
        for right in embeddings[idx + 1 :]:
            similarity = float(sum(lv * rv for lv, rv in zip(left, right)))
            min_similarity = min(min_similarity, similarity)
    return min_similarity >= settings.coherence_semantic_similarity_threshold


def _semantic_text_for_match(market: MarketInfo) -> str:
    question_without_date = DATE_FRAGMENT.sub("", market.question).strip()
    if market.description:
        return f"{question_without_date}\n{market.description.strip()}"
    return question_without_date


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
        logger.info("Loaded semantic model for coherence: %s", settings.coherence_semantic_model_name)
        return _semantic_model
    except Exception as exc:  # pragma: no cover - depends on runtime env/model cache
        _semantic_model_failed = True
        logger.warning("Semantic fallback unavailable (%s): %s", settings.coherence_semantic_model_name, exc)
        return None

