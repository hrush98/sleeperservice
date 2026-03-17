"""Dataset discovery and profiling helpers for Phase 0.5."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from services.research.settings import HistoricalResearchSettings

KNOWN_VENUES = ("polymarket", "kalshi")
TIMESTAMP_NAME_HINTS = (
    "time",
    "timestamp",
    "date",
    "created",
    "updated",
    "resolved",
    "resolution",
    "closed",
    "open",
)
RESOLUTION_FIELD_HINTS = (
    "resolution",
    "resolved",
    "result",
    "winner",
    "outcome",
    "closed",
)
METADATA_FIELD_HINTS = (
    "market_id",
    "event_id",
    "market_slug",
    "question",
    "title",
    "condition_id",
    "venue",
)
DUPLICATE_KEY_CANDIDATES = (
    ("trade_id",),
    ("id",),
    ("tx_hash", "log_index"),
    ("transaction_hash", "log_index"),
    ("transaction_hash", "trade_index"),
    ("market_id", "timestamp", "price", "size"),
)


@dataclass(frozen=True)
class DiscoveredDatasetFile:
    relative_path: str
    absolute_path: str
    extension: str
    size_bytes: int
    modified_at: str
    guessed_venue: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "absolute_path": self.absolute_path,
            "extension": self.extension,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "guessed_venue": self.guessed_venue,
        }


@dataclass(frozen=True)
class DatasetProfileArtifacts:
    manifest_path: Path
    summary_path: Path
    manifest: dict[str, Any]


def discover_dataset_files(
    dataset_root: Path,
    *,
    include_hidden: bool = False,
    max_files: int | None = None,
) -> list[DiscoveredDatasetFile]:
    discovered: list[DiscoveredDatasetFile] = []

    for file_path in sorted(dataset_root.rglob("*")):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(dataset_root)
        if not include_hidden and any(part.startswith(".") for part in relative_path.parts):
            continue

        stat = file_path.stat()
        discovered.append(
            DiscoveredDatasetFile(
                relative_path=relative_path.as_posix(),
                absolute_path=str(file_path.resolve()),
                extension=file_path.suffix.lower() or "<none>",
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                guessed_venue=_guess_venue(relative_path),
            )
        )
        if max_files is not None and len(discovered) >= max_files:
            break

    return discovered


def profile_dataset(
    research_settings: HistoricalResearchSettings,
    *,
    include_hidden: bool = False,
    max_files: int | None = None,
) -> DatasetProfileArtifacts:
    dataset_root = research_settings.require_dataset_root()
    if not dataset_root.exists():
        raise FileNotFoundError(f"Historical dataset root does not exist: {dataset_root}")
    if not dataset_root.is_dir():
        raise NotADirectoryError(f"Historical dataset root is not a directory: {dataset_root}")

    files = discover_dataset_files(
        dataset_root,
        include_hidden=include_hidden,
        max_files=max_files,
    )

    generated_at = datetime.now(tz=UTC)
    run_id = generated_at.strftime("%Y%m%dT%H%M%SZ")

    warnings: list[str] = []
    if not files:
        warnings.append("No files were discovered under the configured dataset root.")

    inventory = _build_inventory(files)
    parquet_files = [record for record in files if record.extension == ".parquet"]

    parquet_capabilities = {
        "duckdb_available": False,
        "parquet_audit_performed": False,
    }
    parquet_audit: dict[str, Any] = {
        "file_count": len(parquet_files),
        "warnings": [],
        "files": [],
    }

    if parquet_files:
        parquet_audit, parquet_capabilities = _profile_parquet_files(parquet_files)
        warnings.extend(parquet_audit["warnings"])
    else:
        warnings.append("No parquet files were discovered. Dataset landing can continue, but parquet-specific profiling was skipped.")

    manifest = {
        "generated_at": generated_at.isoformat(),
        "run_id": run_id,
        "dataset_root": str(dataset_root),
        "output_root": str(research_settings.output_root),
        "inventory": inventory,
        "capabilities": parquet_capabilities,
        "warnings": _unique_preserving_order(warnings),
        "files": [record.to_dict() for record in files],
        "parquet_audit": parquet_audit,
    }

    manifest_path, summary_path = _write_profile_outputs(
        research_settings.output_root,
        run_id,
        manifest,
    )

    return DatasetProfileArtifacts(
        manifest_path=manifest_path,
        summary_path=summary_path,
        manifest=manifest,
    )


def _guess_venue(relative_path: Path) -> str:
    lower_parts = [part.lower() for part in relative_path.parts]
    for venue in KNOWN_VENUES:
        if any(venue in part for part in lower_parts):
            return venue
    return "unknown"


def _build_inventory(files: list[DiscoveredDatasetFile]) -> dict[str, Any]:
    extension_counts = Counter(record.extension for record in files)
    venue_counts = Counter(record.guessed_venue for record in files)
    total_size_bytes = sum(record.size_bytes for record in files)

    return {
        "total_files": len(files),
        "total_size_bytes": total_size_bytes,
        "extensions": dict(sorted(extension_counts.items())),
        "venues": dict(sorted(venue_counts.items())),
    }


def _profile_parquet_files(
    parquet_files: list[DiscoveredDatasetFile],
) -> tuple[dict[str, Any], dict[str, bool]]:
    try:
        import duckdb
    except ImportError:
        return (
            {
                "file_count": len(parquet_files),
                "warnings": [
                    "duckdb is not installed in the current environment, so row counts, schema details, null rates, timestamp sanity checks, resolution coverage, and duplicate-risk checks were skipped."
                ],
                "files": [],
            },
            {
                "duckdb_available": False,
                "parquet_audit_performed": False,
            },
        )

    connection = duckdb.connect(database=":memory:")
    try:
        file_reports: list[dict[str, Any]] = []
        warnings: list[str] = []
        for parquet_file in parquet_files:
            try:
                file_report, file_warnings = _profile_single_parquet_file(connection, parquet_file)
            except Exception as exc:  # pragma: no cover - exercised via focused tests
                file_report = {
                    "relative_path": parquet_file.relative_path,
                    "row_count": None,
                    "column_count": None,
                    "columns": [],
                    "top_null_columns": [],
                    "timestamp_checks": [],
                    "resolution_coverage": [],
                    "duplicate_check": {
                        "status": "error",
                        "columns": [],
                        "duplicate_rows": None,
                    },
                    "metadata_quality": {
                        "present_fields": [],
                        "missing_fields": list(METADATA_FIELD_HINTS),
                    },
                    "error": str(exc),
                }
                file_warnings = [
                    f"{parquet_file.relative_path}: parquet profiling failed: {exc}"
                ]
            file_reports.append(file_report)
            warnings.extend(file_warnings)
    finally:
        connection.close()

    return (
        {
            "file_count": len(parquet_files),
            "warnings": _unique_preserving_order(warnings),
            "files": file_reports,
        },
        {
            "duckdb_available": True,
            "parquet_audit_performed": True,
        },
    )


def _profile_single_parquet_file(connection: Any, parquet_file: DiscoveredDatasetFile) -> tuple[dict[str, Any], list[str]]:
    relation = f"read_parquet({_sql_string_literal(parquet_file.absolute_path)})"
    warnings: list[str] = []

    schema_rows = connection.execute(f"DESCRIBE SELECT * FROM {relation}").fetchall()
    columns = [
        {
            "name": row[0],
            "type": row[1],
            "nullable": str(row[2]).upper() != "NO",
        }
        for row in schema_rows
    ]
    row_count = int(connection.execute(f"SELECT COUNT(*) FROM {relation}").fetchone()[0])

    null_counts = _compute_null_counts(connection, relation, columns, row_count)
    timestamp_checks = _compute_timestamp_checks(connection, relation, columns)
    duplicate_check = _compute_duplicate_check(connection, relation, columns)
    resolution_coverage = _compute_non_null_rates(columns, null_counts, row_count, RESOLUTION_FIELD_HINTS)
    metadata_quality = _compute_metadata_quality(columns)

    if duplicate_check["status"] == "skipped":
        warnings.append(
            f"{parquet_file.relative_path}: duplicate-risk check skipped because no known key columns were present."
        )
    if not timestamp_checks:
        warnings.append(
            f"{parquet_file.relative_path}: no timestamp-like columns were detected for timestamp sanity checks."
        )
    if not resolution_coverage:
        warnings.append(
            f"{parquet_file.relative_path}: no resolution-like columns were detected for resolution coverage checks."
        )
    if metadata_quality["missing_fields"]:
        warnings.append(
            f"{parquet_file.relative_path}: metadata fields missing: {', '.join(metadata_quality['missing_fields'])}."
        )

    top_null_columns = sorted(
        (
            {
                "name": column["name"],
                "null_count": null_counts[column["name"]],
                "null_rate": _safe_rate(null_counts[column["name"]], row_count),
            }
            for column in columns
        ),
        key=lambda item: item["null_rate"],
        reverse=True,
    )[:10]

    return (
        {
            "relative_path": parquet_file.relative_path,
            "row_count": row_count,
            "column_count": len(columns),
            "columns": columns,
            "top_null_columns": top_null_columns,
            "timestamp_checks": timestamp_checks,
            "resolution_coverage": resolution_coverage,
            "duplicate_check": duplicate_check,
            "metadata_quality": metadata_quality,
        },
        warnings,
    )


def _compute_null_counts(
    connection: Any,
    relation: str,
    columns: list[dict[str, Any]],
    row_count: int,
) -> dict[str, int]:
    if row_count == 0 or not columns:
        return {column["name"]: 0 for column in columns}

    expressions = []
    for index, column in enumerate(columns):
        identifier = _quote_identifier(column["name"])
        expressions.append(
            f"SUM(CASE WHEN {identifier} IS NULL THEN 1 ELSE 0 END) AS null_count_{index}"
        )
    row = connection.execute(f"SELECT {', '.join(expressions)} FROM {relation}").fetchone()

    return {
        column["name"]: int(row[index] or 0)
        for index, column in enumerate(columns)
    }


def _compute_timestamp_checks(
    connection: Any,
    relation: str,
    columns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    timestamp_columns = [
        column
        for column in columns
        if _looks_like_timestamp(column["name"], column["type"])
    ]
    if not timestamp_columns:
        return []

    expressions: list[str] = []
    for index, column in enumerate(timestamp_columns):
        identifier = _quote_identifier(column["name"])
        expressions.append(f"MIN({identifier}) AS min_ts_{index}")
        expressions.append(f"MAX({identifier}) AS max_ts_{index}")
    row = connection.execute(f"SELECT {', '.join(expressions)} FROM {relation}").fetchone()

    checks = []
    for index, column in enumerate(timestamp_columns):
        min_value = row[index * 2]
        max_value = row[index * 2 + 1]
        checks.append(
            {
                "column": column["name"],
                "min": _serialize_scalar(min_value),
                "max": _serialize_scalar(max_value),
            }
        )
    return checks


def _compute_duplicate_check(
    connection: Any,
    relation: str,
    columns: list[dict[str, Any]],
) -> dict[str, Any]:
    lower_to_actual = {column["name"].lower(): column["name"] for column in columns}

    for candidate in DUPLICATE_KEY_CANDIDATES:
        if not all(name in lower_to_actual for name in candidate):
            continue
        actual_columns = [lower_to_actual[name] for name in candidate]
        quoted_columns = ", ".join(_quote_identifier(name) for name in actual_columns)
        duplicate_rows = connection.execute(
            "SELECT COALESCE(SUM(group_count - 1), 0) "
            f"FROM (SELECT {quoted_columns}, COUNT(*) AS group_count "
            f"FROM {relation} GROUP BY {quoted_columns} HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        return {
            "status": "computed",
            "columns": actual_columns,
            "duplicate_rows": int(duplicate_rows or 0),
        }

    return {
        "status": "skipped",
        "columns": [],
        "duplicate_rows": None,
    }


def _compute_non_null_rates(
    columns: list[dict[str, Any]],
    null_counts: dict[str, int],
    row_count: int,
    field_hints: tuple[str, ...],
) -> list[dict[str, Any]]:
    matching_columns = [
        column["name"]
        for column in columns
        if any(hint in column["name"].lower() for hint in field_hints)
    ]
    return [
        {
            "column": column_name,
            "non_null_count": row_count - null_counts[column_name],
            "non_null_rate": _safe_rate(row_count - null_counts[column_name], row_count),
        }
        for column_name in matching_columns
    ]


def _compute_metadata_quality(columns: list[dict[str, Any]]) -> dict[str, Any]:
    lower_names = {column["name"].lower() for column in columns}
    present_fields = [field for field in METADATA_FIELD_HINTS if field in lower_names]
    missing_fields = [field for field in METADATA_FIELD_HINTS if field not in lower_names]

    return {
        "present_fields": present_fields,
        "missing_fields": missing_fields,
    }


def _looks_like_timestamp(column_name: str, column_type: str) -> bool:
    normalized_name = column_name.lower()
    normalized_type = column_type.upper()
    return (
        "TIMESTAMP" in normalized_type
        or normalized_type == "DATE"
        or any(hint in normalized_name for hint in TIMESTAMP_NAME_HINTS)
    )


def _write_profile_outputs(
    output_root: Path,
    run_id: str,
    manifest: dict[str, Any],
) -> tuple[Path, Path]:
    manifests_dir = output_root / "manifests"
    summaries_dir = output_root / "summaries"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    summaries_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = manifests_dir / f"dataset_profile_{run_id}.json"
    summary_path = summaries_dir / f"dataset_profile_{run_id}.md"

    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    summary_path.write_text(_render_summary(manifest), encoding="utf-8")

    return manifest_path, summary_path


def _render_summary(manifest: dict[str, Any]) -> str:
    inventory = manifest["inventory"]
    parquet_audit = manifest["parquet_audit"]
    capabilities = manifest["capabilities"]

    lines = [
        "# Historical Dataset Profile",
        "",
        f"- Generated at: {manifest['generated_at']}",
        f"- Dataset root: `{manifest['dataset_root']}`",
        f"- Output root: `{manifest['output_root']}`",
        f"- Files discovered: {inventory['total_files']}",
        f"- Total size (bytes): {inventory['total_size_bytes']}",
        f"- DuckDB parquet audit available: {capabilities['duckdb_available']}",
        f"- Parquet audit performed: {capabilities['parquet_audit_performed']}",
        "",
        "## Inventory",
        "",
        f"- Extensions: {_format_counter(inventory['extensions'])}",
        f"- Venues: {_format_counter(inventory['venues'])}",
        "",
        "## Warnings",
        "",
    ]

    if manifest["warnings"]:
        lines.extend(f"- {warning}" for warning in manifest["warnings"])
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Parquet Audit",
            "",
            f"- Parquet files discovered: {parquet_audit['file_count']}",
        ]
    )

    if not parquet_audit["files"]:
        lines.append("- No parquet file details were produced in this run.")
        return "\n".join(lines) + "\n"

    for file_report in parquet_audit["files"]:
        lines.extend(
            [
                "",
                f"### `{file_report['relative_path']}`",
                f"- Rows: {file_report['row_count']}",
                f"- Columns: {file_report['column_count']}",
                f"- Duplicate check: {_format_duplicate_check(file_report['duplicate_check'])}",
                f"- Metadata fields present: {', '.join(file_report['metadata_quality']['present_fields']) or 'none'}",
                f"- Metadata fields missing: {', '.join(file_report['metadata_quality']['missing_fields']) or 'none'}",
            ]
        )

        if file_report["timestamp_checks"]:
            timestamp_summary = "; ".join(
                f"{item['column']} [{item['min']} -> {item['max']}]"
                for item in file_report["timestamp_checks"]
            )
            lines.append(f"- Timestamp checks: {timestamp_summary}")
        else:
            lines.append("- Timestamp checks: none")

        if file_report["resolution_coverage"]:
            resolution_summary = "; ".join(
                f"{item['column']}={item['non_null_rate']:.4f}"
                for item in file_report["resolution_coverage"]
            )
            lines.append(f"- Resolution coverage: {resolution_summary}")
        else:
            lines.append("- Resolution coverage: none")

        if file_report["top_null_columns"]:
            top_nulls = ", ".join(
                f"{item['name']}={item['null_rate']:.4f}"
                for item in file_report["top_null_columns"][:5]
            )
            lines.append(f"- Highest null-rate columns: {top_nulls}")

    return "\n".join(lines) + "\n"


def _format_counter(counter: dict[str, int]) -> str:
    if not counter:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in counter.items())


def _format_duplicate_check(duplicate_check: dict[str, Any]) -> str:
    if duplicate_check["status"] != "computed":
        return "skipped"
    return (
        f"{duplicate_check['duplicate_rows']} duplicate rows over "
        f"{', '.join(duplicate_check['columns'])}"
    )


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _serialize_scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique
