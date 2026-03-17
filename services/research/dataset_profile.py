"""Dataset discovery and profiling helpers for Phase 0.5."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Iterable

from services.research.settings import HistoricalResearchSettings

KNOWN_VENUES = ("polymarket", "kalshi")
FILE_KIND_HINTS: dict[str, tuple[str, ...]] = {
    "legacy_trades": ("legacy_trades",),
    "blocks": ("blocks",),
    "trades": ("trade", "trades", "fills", "executions"),
    "markets": ("market", "markets", "contract", "contracts", "metadata"),
    "resolutions": ("resolution", "resolutions", "resolved", "outcome", "outcomes"),
}
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
    "end",
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
    "ticker",
    "event_ticker",
    "clob_token_ids",
    "market_maker_address",
)
DUPLICATE_KEY_CANDIDATES = (
    ("trade_id",),
    ("id",),
    ("tx_hash", "log_index"),
    ("transaction_hash", "log_index"),
    ("transaction_hash", "trade_index"),
    ("market_id", "timestamp", "price", "size"),
)
MAX_MANIFEST_FILE_RECORDS = 200
DEEP_AUDIT_ROW_LIMIT = 50_000_000
DEEP_AUDIT_SAMPLE_FILE_LIMIT = 12


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
class DatasetCollection:
    collection_id: str
    guessed_venue: str
    kind: str
    relative_dir: str
    absolute_dir: str
    parquet_glob: str
    can_use_glob: bool
    file_count: int
    total_size_bytes: int
    absolute_paths: tuple[str, ...]
    sample_relative_paths: tuple[str, ...]
    sample_absolute_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "collection_id": self.collection_id,
            "guessed_venue": self.guessed_venue,
            "kind": self.kind,
            "relative_dir": self.relative_dir,
            "absolute_dir": self.absolute_dir,
            "parquet_glob": self.parquet_glob,
            "can_use_glob": self.can_use_glob,
            "file_count": self.file_count,
            "total_size_bytes": self.total_size_bytes,
            "sample_relative_paths": list(self.sample_relative_paths),
        }


@dataclass(frozen=True)
class DatasetProfileArtifacts:
    manifest_path: Path
    summary_path: Path
    manifest: dict[str, Any]


def classify_dataset_file_kind(relative_path: str) -> str:
    normalized = relative_path.lower()
    for kind, hints in FILE_KIND_HINTS.items():
        if any(hint in normalized for hint in hints):
            return kind
    return "all"


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
        if file_path.name.startswith("._"):
            continue
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


def build_dataset_collections(
    parquet_files: Iterable[DiscoveredDatasetFile],
) -> list[DatasetCollection]:
    grouped: dict[tuple[str, str, str], list[DiscoveredDatasetFile]] = {}
    directory_totals: Counter[str] = Counter()
    for parquet_file in parquet_files:
        relative_dir = Path(parquet_file.relative_path).parent.as_posix()
        absolute_dir = str(Path(parquet_file.absolute_path).resolve().parent)
        key = (
            parquet_file.guessed_venue,
            classify_dataset_file_kind(parquet_file.relative_path),
            relative_dir,
        )
        grouped.setdefault(key, []).append(parquet_file)
        directory_totals[absolute_dir] += 1

    collections: list[DatasetCollection] = []
    for (venue, kind, relative_dir), records in sorted(grouped.items()):
        sorted_records = sorted(records, key=lambda record: record.relative_path)
        sample_records = _sample_records(sorted_records, DEEP_AUDIT_SAMPLE_FILE_LIMIT)
        absolute_dir = str(Path(sorted_records[0].absolute_path).resolve().parent)
        collections.append(
            DatasetCollection(
                collection_id=_build_collection_id(venue, kind, relative_dir),
                guessed_venue=venue,
                kind=kind,
                relative_dir=relative_dir,
                absolute_dir=absolute_dir,
                parquet_glob=str(Path(absolute_dir) / "[!._]*.parquet"),
                can_use_glob=len(sorted_records) == directory_totals[absolute_dir],
                file_count=len(sorted_records),
                total_size_bytes=sum(record.size_bytes for record in sorted_records),
                absolute_paths=tuple(record.absolute_path for record in sorted_records),
                sample_relative_paths=tuple(record.relative_path for record in sample_records),
                sample_absolute_paths=tuple(record.absolute_path for record in sample_records),
            )
        )
    return collections


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
    parquet_collections = build_dataset_collections(parquet_files)
    manifest_file_records, file_record_strategy = _serialize_manifest_files(files)

    parquet_capabilities = {
        "duckdb_available": False,
        "parquet_audit_performed": False,
    }
    parquet_audit: dict[str, Any] = {
        "file_count": len(parquet_files),
        "collection_count": len(parquet_collections),
        "warnings": [],
        "collections": [],
    }

    if parquet_collections:
        parquet_audit, parquet_capabilities = _profile_parquet_collections(parquet_collections)
        warnings.extend(parquet_audit["warnings"])
    elif parquet_files:
        warnings.append("Parquet files were discovered, but no logical parquet collections could be formed.")
    else:
        warnings.append(
            "No parquet files were discovered. Dataset landing can continue, but parquet-specific profiling was skipped."
        )

    manifest = {
        "generated_at": generated_at.isoformat(),
        "run_id": run_id,
        "dataset_root": str(dataset_root),
        "output_root": str(research_settings.output_root),
        "inventory": inventory,
        "capabilities": parquet_capabilities,
        "warnings": _unique_preserving_order(warnings),
        "file_record_strategy": file_record_strategy,
        "files": manifest_file_records,
        "parquet_collections": [collection.to_dict() for collection in parquet_collections],
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


def _serialize_manifest_files(
    files: list[DiscoveredDatasetFile],
) -> tuple[list[dict[str, Any]], str]:
    if len(files) <= MAX_MANIFEST_FILE_RECORDS:
        return ([record.to_dict() for record in files], "full")

    sample_records = _sample_records(files, MAX_MANIFEST_FILE_RECORDS)
    return (
        [record.to_dict() for record in sample_records],
        f"sampled:{MAX_MANIFEST_FILE_RECORDS}",
    )


def _profile_parquet_collections(
    collections: list[DatasetCollection],
) -> tuple[dict[str, Any], dict[str, bool]]:
    try:
        import duckdb
    except ImportError:
        return (
            {
                "file_count": sum(collection.file_count for collection in collections),
                "collection_count": len(collections),
                "warnings": [
                    "duckdb is not installed in the current environment, so parquet schema, row-count, null-rate, timestamp, resolution-coverage, and duplicate-risk checks were skipped."
                ],
                "collections": [],
            },
            {
                "duckdb_available": False,
                "parquet_audit_performed": False,
            },
        )

    connection = duckdb.connect(database=":memory:")
    try:
        collection_reports: list[dict[str, Any]] = []
        warnings: list[str] = []
        for collection in collections:
            try:
                collection_report, collection_warnings = _profile_single_collection(
                    connection,
                    collection,
                )
            except Exception as exc:  # pragma: no cover - exercised via focused tests
                collection_report = {
                    "collection_id": collection.collection_id,
                    "relative_dir": collection.relative_dir,
                    "venue": collection.guessed_venue,
                    "kind": collection.kind,
                    "file_count": collection.file_count,
                    "sampled_file_count": len(collection.sample_absolute_paths),
                    "audit_scope": "error",
                    "row_count": None,
                    "audited_row_count": None,
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
                    "sample_relative_paths": list(collection.sample_relative_paths),
                }
                collection_warnings = [
                    f"{collection.relative_dir}: parquet profiling failed: {exc}"
                ]
            collection_reports.append(collection_report)
            warnings.extend(collection_warnings)
    finally:
        connection.close()

    return (
        {
            "file_count": sum(collection.file_count for collection in collections),
            "collection_count": len(collections),
            "warnings": _unique_preserving_order(warnings),
            "collections": collection_reports,
        },
        {
            "duckdb_available": True,
            "parquet_audit_performed": True,
        },
    )


def _profile_single_collection(
    connection: Any,
    collection: DatasetCollection,
) -> tuple[dict[str, Any], list[str]]:
    relation = _read_parquet_collection_sql(collection)
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

    audit_relation = relation
    audit_scope = "full"
    audited_row_count = row_count
    sampled_file_count = collection.file_count

    if row_count > DEEP_AUDIT_ROW_LIMIT and len(collection.sample_absolute_paths) < collection.file_count:
        audit_relation = _read_parquet_files_sql(collection.sample_absolute_paths)
        audit_scope = "sample"
        audited_row_count = int(connection.execute(f"SELECT COUNT(*) FROM {audit_relation}").fetchone()[0])
        sampled_file_count = len(collection.sample_absolute_paths)
        warnings.append(
            f"{collection.relative_dir}: deep parquet checks used {sampled_file_count} sampled files because the collection has {row_count} rows."
        )

    null_counts = _compute_null_counts(connection, audit_relation, columns, audited_row_count)
    timestamp_checks = _compute_timestamp_checks(connection, audit_relation, columns)
    duplicate_check = _compute_duplicate_check(connection, audit_relation, columns)
    resolution_coverage = _compute_non_null_rates(columns, null_counts, audited_row_count, RESOLUTION_FIELD_HINTS)
    metadata_quality = _compute_metadata_quality(columns)

    if duplicate_check["status"] == "skipped":
        warnings.append(
            f"{collection.relative_dir}: duplicate-risk check skipped because no known key columns were present."
        )
    if not timestamp_checks:
        warnings.append(
            f"{collection.relative_dir}: no timestamp-like columns were detected for timestamp sanity checks."
        )
    if not resolution_coverage:
        warnings.append(
            f"{collection.relative_dir}: no resolution-like columns were detected for resolution coverage checks."
        )
    if metadata_quality["missing_fields"]:
        warnings.append(
            f"{collection.relative_dir}: metadata fields missing: {', '.join(metadata_quality['missing_fields'])}."
        )

    top_null_columns = sorted(
        (
            {
                "name": column["name"],
                "null_count": null_counts[column["name"]],
                "null_rate": _safe_rate(null_counts[column["name"]], audited_row_count),
            }
            for column in columns
        ),
        key=lambda item: item["null_rate"],
        reverse=True,
    )[:10]

    return (
        {
            "collection_id": collection.collection_id,
            "relative_dir": collection.relative_dir,
            "parquet_glob": collection.parquet_glob,
            "venue": collection.guessed_venue,
            "kind": collection.kind,
            "file_count": collection.file_count,
            "sampled_file_count": sampled_file_count,
            "audit_scope": audit_scope,
            "row_count": row_count,
            "audited_row_count": audited_row_count,
            "column_count": len(columns),
            "columns": columns,
            "top_null_columns": top_null_columns,
            "timestamp_checks": timestamp_checks,
            "resolution_coverage": resolution_coverage,
            "duplicate_check": duplicate_check,
            "metadata_quality": metadata_quality,
            "sample_relative_paths": list(collection.sample_relative_paths),
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
        expressions.append(f"CAST(MIN({identifier}) AS VARCHAR) AS min_ts_{index}")
        expressions.append(f"CAST(MAX({identifier}) AS VARCHAR) AS max_ts_{index}")
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
        f"- File records stored in manifest: {len(manifest['files'])} ({manifest['file_record_strategy']})",
        f"- Parquet collections discovered: {len(manifest['parquet_collections'])}",
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
            f"- Parquet collections discovered: {parquet_audit['collection_count']}",
        ]
    )

    if not parquet_audit["collections"]:
        lines.append("- No parquet collection details were produced in this run.")
        return "\n".join(lines) + "\n"

    for collection_report in parquet_audit["collections"]:
        lines.extend(
            [
                "",
                f"### `{collection_report['relative_dir']}`",
                f"- Venue/kind: {collection_report['venue']}/{collection_report['kind']}",
                f"- Files: {collection_report['file_count']}",
                f"- Rows: {collection_report['row_count']}",
                f"- Deep-audit scope: {collection_report['audit_scope']} ({collection_report['sampled_file_count']} files, {collection_report['audited_row_count']} rows)",
                f"- Columns: {collection_report['column_count']}",
                f"- Duplicate check: {_format_duplicate_check(collection_report['duplicate_check'])}",
                f"- Metadata fields present: {', '.join(collection_report['metadata_quality']['present_fields']) or 'none'}",
                f"- Metadata fields missing: {', '.join(collection_report['metadata_quality']['missing_fields']) or 'none'}",
            ]
        )

        if collection_report["timestamp_checks"]:
            timestamp_summary = "; ".join(
                f"{item['column']} [{item['min']} -> {item['max']}]"
                for item in collection_report["timestamp_checks"]
            )
            lines.append(f"- Timestamp checks: {timestamp_summary}")
        else:
            lines.append("- Timestamp checks: none")

        if collection_report["resolution_coverage"]:
            resolution_summary = "; ".join(
                f"{item['column']}={item['non_null_rate']:.4f}"
                for item in collection_report["resolution_coverage"]
            )
            lines.append(f"- Resolution coverage: {resolution_summary}")
        else:
            lines.append("- Resolution coverage: none")

        if collection_report["top_null_columns"]:
            top_nulls = ", ".join(
                f"{item['name']}={item['null_rate']:.4f}"
                for item in collection_report["top_null_columns"][:5]
            )
            lines.append(f"- Highest null-rate columns: {top_nulls}")

        if collection_report["sample_relative_paths"]:
            lines.append(
                "- Sample files: "
                + ", ".join(collection_report["sample_relative_paths"][:5])
            )

    return "\n".join(lines) + "\n"


def _build_collection_id(venue: str, kind: str, relative_dir: str) -> str:
    raw_value = f"{venue}_{kind}_{relative_dir or 'root'}"
    return "".join(character if character.isalnum() else "_" for character in raw_value).strip("_").lower()


def _sample_records(records: list[DiscoveredDatasetFile], limit: int) -> list[DiscoveredDatasetFile]:
    if limit <= 0 or not records:
        return []
    if len(records) <= limit:
        return list(records)
    if limit == 1:
        return [records[0]]

    max_index = len(records) - 1
    indexes = {
        round(position * max_index / (limit - 1))
        for position in range(limit)
    }
    return [records[index] for index in sorted(indexes)]


def _read_parquet_glob_sql(glob_path: str) -> str:
    return f"read_parquet({_sql_string_literal(glob_path)}, union_by_name=true)"


def _read_parquet_files_sql(paths: Iterable[str]) -> str:
    quoted_paths = ", ".join(_sql_string_literal(path) for path in paths)
    return f"read_parquet([{quoted_paths}], union_by_name=true)"


def _read_parquet_collection_sql(collection: DatasetCollection) -> str:
    if collection.can_use_glob:
        return _read_parquet_glob_sql(collection.parquet_glob)
    return _read_parquet_files_sql(collection.absolute_paths)


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
