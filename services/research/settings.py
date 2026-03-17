"""Settings for Phase 0.5 historical research tooling."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

DEFAULT_HISTORICAL_RESEARCH_OUTPUT_ROOT = Path("logs/historical_research")


def _resolve_path(raw_path: str, *, cwd: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


@dataclass(frozen=True)
class HistoricalResearchSettings:
    """Resolved local paths for read-only historical research work."""

    dataset_root: Path | None
    output_root: Path

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        cwd: Path | None = None,
    ) -> "HistoricalResearchSettings":
        env = environ if environ is not None else os.environ
        current_dir = (cwd or Path.cwd()).resolve()

        dataset_root_raw = env.get("HISTORICAL_DATASET_ROOT", "").strip()
        output_root_raw = env.get("HISTORICAL_RESEARCH_OUTPUT_ROOT", "").strip()

        dataset_root = None
        if dataset_root_raw:
            dataset_root = _resolve_path(dataset_root_raw, cwd=current_dir)

        output_root_source = output_root_raw or str(DEFAULT_HISTORICAL_RESEARCH_OUTPUT_ROOT)
        output_root = _resolve_path(output_root_source, cwd=current_dir)

        return cls(dataset_root=dataset_root, output_root=output_root)

    def require_dataset_root(self) -> Path:
        if self.dataset_root is None:
            raise ValueError(
                "Historical dataset root is not configured. "
                "Set HISTORICAL_DATASET_ROOT or pass --dataset-root."
            )
        return self.dataset_root
