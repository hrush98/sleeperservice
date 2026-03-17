"""Shared helpers for Phase 0.5 empirical studies."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from typing import Any, Iterable

from services.research.features import PRICE_BUCKETS, TIME_TO_RESOLUTION_BUCKETS

REPO_ROOT = Path(__file__).resolve().parents[3]


def build_run_id(now: datetime | None = None) -> str:
    generated_at = now or datetime.now(tz=UTC)
    return generated_at.strftime("%Y%m%dT%H%M%SZ")


def default_studies_root(output_root: Path) -> Path:
    return output_root / "studies"


def default_database_path(output_root: Path) -> Path:
    return output_root / "duckdb" / "historical_research.duckdb"


def sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def normalize_binary_outcome_sql(expr: str) -> str:
    return (
        "CASE "
        f"WHEN {expr} IS NULL THEN NULL "
        f"WHEN LOWER(TRIM(CAST({expr} AS VARCHAR))) IN ('yes', 'y', 'true', 't', '1') THEN 'yes' "
        f"WHEN LOWER(TRIM(CAST({expr} AS VARCHAR))) IN ('no', 'n', 'false', 'f', '0') THEN 'no' "
        "ELSE NULL END"
    )


def normalize_trade_direction_sql(expr: str) -> str:
    return (
        "CASE "
        f"WHEN {expr} IS NULL THEN NULL "
        f"WHEN LOWER(TRIM(CAST({expr} AS VARCHAR))) IN ('buy', 'bought', 'bid', 'long', 'yes', 'no') THEN 'buy' "
        f"WHEN LOWER(TRIM(CAST({expr} AS VARCHAR))) IN ('sell', 'sold', 'ask', 'short', 'lay') THEN 'sell' "
        "ELSE NULL END"
    )


def invert_trade_direction_sql(expr: str) -> str:
    return (
        "CASE "
        f"WHEN {expr} = 'buy' THEN 'sell' "
        f"WHEN {expr} = 'sell' THEN 'buy' "
        "ELSE NULL END"
    )


def contract_outcome_indicator_sql(contract_side_expr: str, resolved_outcome_expr: str) -> str:
    normalized_contract_expr = normalize_binary_outcome_sql(contract_side_expr)
    normalized_resolved_expr = normalize_binary_outcome_sql(resolved_outcome_expr)
    return (
        "CASE "
        f"WHEN {normalized_contract_expr} IS NULL OR {normalized_resolved_expr} IS NULL THEN NULL "
        f"WHEN {normalized_contract_expr} = {normalized_resolved_expr} THEN 1.0 "
        "ELSE 0.0 END"
    )


def position_pnl_per_contract_sql(
    *,
    position_side_expr: str,
    price_probability_expr: str,
    contract_outcome_expr: str,
) -> str:
    return (
        "CASE "
        f"WHEN {position_side_expr} = 'buy' THEN {contract_outcome_expr} - {price_probability_expr} "
        f"WHEN {position_side_expr} = 'sell' THEN {price_probability_expr} - {contract_outcome_expr} "
        "ELSE NULL END"
    )


def bucket_order_case(expr: str, labels: Iterable[str], *, unknown_order: int = 999) -> str:
    cases = [
        f"WHEN {expr} = {sql_string_literal(label)} THEN {index}"
        for index, label in enumerate(labels)
    ]
    return "CASE " + " ".join(cases) + f" ELSE {unknown_order} END"


def price_bucket_order_case(expr: str) -> str:
    labels = [label for _, label in PRICE_BUCKETS] + ["gt_100c"]
    return bucket_order_case(expr, labels)


def time_bucket_order_case(expr: str) -> str:
    labels = ["negative"] + [label for _, label in TIME_TO_RESOLUTION_BUCKETS] + ["gte_90d"]
    return bucket_order_case(expr, labels)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_text(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def file_fingerprint(path: Path) -> dict[str, Any]:
    stat_result = path.stat()
    return {
        "path": str(path),
        "size_bytes": stat_result.st_size,
        "modified_at": datetime.fromtimestamp(stat_result.st_mtime, tz=UTC).isoformat(),
    }


def resolve_code_identity(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    commit = _run_git_command(repo_root, ["rev-parse", "HEAD"])
    short_commit = _run_git_command(repo_root, ["rev-parse", "--short", "HEAD"])
    status_output = _run_git_command(repo_root, ["status", "--short", "--untracked-files=no"])
    return {
        "repo_root": str(repo_root),
        "git_commit": commit or "unknown",
        "git_short_commit": short_commit or "unknown",
        "git_dirty": bool(status_output),
    }


def resolve_source_metadata(output_root: Path, database_path: Path) -> dict[str, Any]:
    database = file_fingerprint(database_path)
    materialization = _find_matching_materialization_metadata(output_root / "normalized", database_path)
    dataset_manifest = _find_latest_json(output_root / "manifests", "dataset_profile_*.json")
    return {
        "database": database,
        "materialization": materialization,
        "dataset_manifest": dataset_manifest,
    }


def _find_matching_materialization_metadata(directory: Path, database_path: Path) -> dict[str, Any] | None:
    candidates = sorted(directory.glob("materialization_*.json"), reverse=True)
    normalized_database_path = str(database_path.resolve())
    for candidate in candidates:
        payload = _load_json(candidate)
        if payload is None:
            continue
        if str(Path(payload.get("database_path", "")).resolve()) == normalized_database_path:
            return {
                "path": str(candidate),
                "run_id": payload.get("run_id"),
                "generated_at": payload.get("generated_at"),
                "dataset_root": payload.get("dataset_root"),
                "output_root": payload.get("output_root"),
            }
    if not candidates:
        return None
    payload = _load_json(candidates[0])
    if payload is None:
        return None
    return {
        "path": str(candidates[0]),
        "run_id": payload.get("run_id"),
        "generated_at": payload.get("generated_at"),
        "dataset_root": payload.get("dataset_root"),
        "output_root": payload.get("output_root"),
    }


def _find_latest_json(directory: Path, pattern: str) -> dict[str, Any] | None:
    candidates = sorted(directory.glob(pattern), reverse=True)
    if not candidates:
        return None
    payload = _load_json(candidates[0])
    if payload is None:
        return None
    return {
        "path": str(candidates[0]),
        "run_id": payload.get("run_id"),
        "generated_at": payload.get("generated_at"),
        "dataset_root": payload.get("dataset_root"),
        "warning_count": len(payload.get("warnings", [])),
    }


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _run_git_command(repo_root: Path, args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() or None
