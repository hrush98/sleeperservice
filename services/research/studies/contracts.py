"""Contracts for durable historical-study artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StudyRunContext:
    """Shared execution context for one study bundle run."""

    run_id: str
    bundle_name: str
    generated_at: datetime
    database_path: Path
    output_root: Path
    run_dir: Path
    venue: str | None
    code_identity: dict[str, Any]
    source_metadata: dict[str, Any]


@dataclass(frozen=True)
class StudyArtifacts:
    """Artifact paths and metadata for one completed study."""

    study_name: str
    study_version: str
    run_id: str
    output_dir: Path
    metadata_path: Path
    summary_path: Path
    data_paths: tuple[Path, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class StudyBundleArtifacts:
    """Top-level bundle artifacts for one historical-study run."""

    run_id: str
    bundle_name: str
    run_dir: Path
    bundle_metadata_path: Path
    bundle_summary_path: Path
    studies: tuple[StudyArtifacts, ...]
    metadata: dict[str, Any]
