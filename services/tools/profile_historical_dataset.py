"""Profile a local historical prediction-market dataset."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
from typing import Sequence

from services.research.dataset_profile import profile_dataset
from services.research.settings import HistoricalResearchSettings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Profile an external historical dataset and write a machine-readable manifest "
            "plus a human-readable summary under HISTORICAL_RESEARCH_OUTPUT_ROOT."
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
        "--max-files",
        type=int,
        default=None,
        help="Optional cap on discovered files for a lighter profiling pass.",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden files and directories during discovery.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit artifact paths as JSON instead of a short text summary.",
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

    artifacts = profile_dataset(
        research_settings,
        include_hidden=args.include_hidden,
        max_files=args.max_files,
    )

    payload = {
        "manifest_path": str(artifacts.manifest_path),
        "summary_path": str(artifacts.summary_path),
        "warning_count": len(artifacts.manifest["warnings"]),
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Manifest: {artifacts.manifest_path}")
        print(f"Summary: {artifacts.summary_path}")
        print(f"Warnings: {payload['warning_count']}")

    return 0


def _resolve_cli_path(path: Path) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return expanded.resolve()


if __name__ == "__main__":
    raise SystemExit(main())

