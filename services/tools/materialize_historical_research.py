"""Materialize normalized historical research tables into a local DuckDB database."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
from typing import Sequence

from services.research.materialize import materialize_historical_research
from services.research.settings import HistoricalResearchSettings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build normalized historical research tables in a local DuckDB database under "
            "HISTORICAL_RESEARCH_OUTPUT_ROOT."
        )
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        help="Override HISTORICAL_DATASET_ROOT for this run.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        help="Override HISTORICAL_RESEARCH_OUTPUT_ROOT for this run.",
    )
    parser.add_argument(
        "--database-path",
        type=Path,
        help="Optional explicit path for the output DuckDB database file.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Optional cap on discovered files for a lighter build pass.",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden files and directories during discovery.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit artifact paths and row counts as JSON instead of a short text summary.",
    )
    parser.add_argument(
        "--skip-view-row-counts",
        action="store_true",
        help="Skip final row-count verification on the normalized views. Useful for very large datasets.",
    )
    parser.add_argument(
        "--skip-bucket-stats",
        action="store_true",
        help="Skip building historical_bucket_stats when a study-ready DuckDB build is enough.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    research_settings = HistoricalResearchSettings.from_env()
    if args.dataset_root is not None:
        research_settings = replace(
            research_settings,
            dataset_root=_resolve_cli_path(args.dataset_root),
        )
    if args.output_root is not None:
        research_settings = replace(
            research_settings,
            output_root=_resolve_cli_path(args.output_root),
        )

    if research_settings.dataset_root is None:
        parser.error("set HISTORICAL_DATASET_ROOT or pass --dataset-root")

    database_path = _resolve_cli_path(args.database_path) if args.database_path is not None else None
    artifacts = materialize_historical_research(
        research_settings,
        database_path=database_path,
        include_hidden=args.include_hidden,
        max_files=args.max_files,
        collect_view_row_counts=not args.skip_view_row_counts,
        include_bucket_stats=not args.skip_bucket_stats,
    )

    payload = {
        "database_path": str(artifacts.database_path),
        "metadata_path": str(artifacts.metadata_path),
        "summary_path": str(artifacts.summary_path),
        "view_row_counts": artifacts.metadata["view_row_counts"],
        "include_bucket_stats": artifacts.metadata["include_bucket_stats"],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"DuckDB database: {artifacts.database_path}")
        print(f"Metadata: {artifacts.metadata_path}")
        print(f"Summary: {artifacts.summary_path}")
        for view_name, row_count in artifacts.metadata["view_row_counts"].items():
            print(f"{view_name}: {row_count if row_count is not None else 'not collected'}")

    return 0


def _resolve_cli_path(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return expanded.resolve()


if __name__ == "__main__":
    raise SystemExit(main())
