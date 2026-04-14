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


@dataclass(frozen=True)
class ArtifactBucketSettings:
    """S3-compatible storage settings for promoted historical artifacts."""

    enabled: bool
    bucket_name: str
    prefix: str
    endpoint_url: str | None
    region: str | None
    access_key_id: str | None
    secret_access_key: str | None
    session_token: str | None

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "ArtifactBucketSettings":
        env = environ if environ is not None else os.environ
        return cls(
            enabled=_parse_bool(env.get("ARTIFACT_BUCKET_ENABLED"), default=False),
            bucket_name=(
                env.get("ARTIFACT_BUCKET_NAME")
                or env.get("BUCKET_NAME")
                or ""
            ).strip(),
            prefix=(env.get("ARTIFACT_BUCKET_PREFIX") or "").strip().strip("/"),
            endpoint_url=(
                env.get("ARTIFACT_BUCKET_ENDPOINT_URL")
                or env.get("AWS_ENDPOINT_URL")
                or None
            ),
            region=(
                env.get("ARTIFACT_BUCKET_REGION")
                or env.get("AWS_REGION")
                or env.get("AWS_DEFAULT_REGION")
                or None
            ),
            access_key_id=(
                env.get("ARTIFACT_BUCKET_ACCESS_KEY_ID")
                or env.get("AWS_ACCESS_KEY_ID")
                or None
            ),
            secret_access_key=(
                env.get("ARTIFACT_BUCKET_SECRET_ACCESS_KEY")
                or env.get("AWS_SECRET_ACCESS_KEY")
                or None
            ),
            session_token=(
                env.get("ARTIFACT_BUCKET_SESSION_TOKEN")
                or env.get("AWS_SESSION_TOKEN")
                or None
            ),
        )

    def object_key(self, relative_path: str) -> str:
        relative = relative_path.strip("/")
        if not self.prefix:
            return relative
        return f"{self.prefix}/{relative}" if relative else self.prefix


def _parse_bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default
