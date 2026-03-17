"""Run durable historical empirical studies from the normalized research DuckDB layer."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
from typing import Sequence

from services.research.settings import HistoricalResearchSettings
from services.research.studies import AVAILABLE_STUDY_NAMES, run_historical_studies


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run durable historical empirical studies from the normalized research DuckDB "
            "database under HISTORICAL_RESEARCH_OUTPUT_ROOT."
        )
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        help="Optional dataset root override. Reserved for consistency with other research tools.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        help="Override HISTORICAL_RESEARCH_OUTPUT_ROOT for this run.",
    )
    parser.add_argument(
        "--database-path",
        type=Path,
        help="Override the normalized research DuckDB path for this run.",
    )
    parser.add_argument(
        "--study",
        action="append",
        choices=AVAILABLE_STUDY_NAMES,
        help="Run only the named study. Repeat the flag to run multiple studies.",
    )
    parser.add_argument(
        "--venue",
        choices=("kalshi", "polymarket"),
        help="Restrict the study run to one venue.",
    )
    parser.add_argument(
        "--run-id",
        help="Optional explicit run id for deterministic artifact paths.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit bundle artifact paths as JSON instead of a short text summary.",
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

    database_path = _resolve_cli_path(args.database_path) if args.database_path is not None else None
    artifacts = run_historical_studies(
        research_settings,
        database_path=database_path,
        study_names=args.study,
        venue=args.venue,
        run_id=args.run_id,
    )

    payload = {
        "run_dir": str(artifacts.run_dir),
        "bundle_metadata_path": str(artifacts.bundle_metadata_path),
        "bundle_summary_path": str(artifacts.bundle_summary_path),
        "study_names": [study.study_name for study in artifacts.studies],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Run directory: {artifacts.run_dir}")
        print(f"Bundle metadata: {artifacts.bundle_metadata_path}")
        print(f"Bundle summary: {artifacts.bundle_summary_path}")
        for study in artifacts.studies:
            print(f"{study.study_name}: {study.summary_path}")

    return 0


def _resolve_cli_path(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return expanded.resolve()


if __name__ == "__main__":
    raise SystemExit(main())
