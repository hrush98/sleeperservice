"""Bundle runner for durable historical empirical studies."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from services.research.studies.bias import run_longshot_favorite_bias_study
from services.research.settings import HistoricalResearchSettings
from services.research.studies.calibration import run_calibration_study
from services.research.studies.common import (
    build_run_id,
    default_database_path,
    default_studies_root,
    resolve_code_identity,
    resolve_source_metadata,
    write_json,
    write_text,
)
from services.research.studies.contracts import StudyArtifacts, StudyBundleArtifacts, StudyRunContext
from services.research.studies.execution import run_maker_taker_expectancy_study
from services.research.studies.sizing import run_sizing_priors_study

AVAILABLE_STUDY_NAMES: tuple[str, ...] = (
    "calibration",
    "maker_taker_expectancy",
    "longshot_favorite_bias",
    "sizing_priors",
)
BASELINE_STUDY_NAMES: tuple[str, ...] = AVAILABLE_STUDY_NAMES

StudyExecutor = Callable[[object, StudyRunContext], StudyArtifacts]

_STUDY_EXECUTORS: dict[str, StudyExecutor] = {
    "calibration": run_calibration_study,
    "maker_taker_expectancy": run_maker_taker_expectancy_study,
    "longshot_favorite_bias": run_longshot_favorite_bias_study,
    "sizing_priors": run_sizing_priors_study,
}


def run_historical_studies(
    research_settings: HistoricalResearchSettings,
    *,
    database_path: Path | None = None,
    study_names: list[str] | tuple[str, ...] | None = None,
    venue: str | None = None,
    run_id: str | None = None,
) -> StudyBundleArtifacts:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "duckdb is required for historical empirical studies. "
            "Install it in the sleeperservice environment before running this command."
        ) from exc

    output_root = research_settings.output_root
    db_path = (database_path or default_database_path(output_root)).expanduser().resolve()
    if not db_path.exists():
        raise FileNotFoundError(
            "Historical research DuckDB database does not exist. "
            f"Expected: {db_path}. Run services.tools.materialize_historical_research first "
            "or pass --database-path."
        )

    selected_studies = _normalize_requested_studies(study_names)
    generated_at = datetime.now(tz=UTC)
    resolved_run_id = run_id or build_run_id(generated_at)
    run_dir = default_studies_root(output_root) / resolved_run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    context = StudyRunContext(
        run_id=resolved_run_id,
        bundle_name="baseline",
        generated_at=generated_at,
        database_path=db_path,
        output_root=output_root,
        run_dir=run_dir,
        venue=venue,
        code_identity=resolve_code_identity(),
        source_metadata=resolve_source_metadata(output_root, db_path),
    )

    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        study_artifacts = tuple(
            _STUDY_EXECUTORS[study_name](connection, context)
            for study_name in selected_studies
        )
    finally:
        connection.close()

    bundle_metadata = {
        "bundle_name": context.bundle_name,
        "run_id": context.run_id,
        "generated_at": context.generated_at.isoformat(),
        "venue_filter": venue,
        "database": context.source_metadata["database"],
        "materialization": context.source_metadata.get("materialization"),
        "dataset_manifest": context.source_metadata.get("dataset_manifest"),
        "code_identity": context.code_identity,
        "study_names": list(selected_studies),
        "study_outputs": {
            artifacts.study_name: {
                "study_version": artifacts.study_version,
                "output_dir": str(artifacts.output_dir),
                "metadata_path": str(artifacts.metadata_path),
                "summary_path": str(artifacts.summary_path),
                "data_paths": [str(path) for path in artifacts.data_paths],
            }
            for artifacts in study_artifacts
        },
    }
    bundle_metadata_path = run_dir / "bundle_metadata.json"
    bundle_summary_path = run_dir / "summary.md"
    write_json(bundle_metadata_path, bundle_metadata)
    write_text(bundle_summary_path, _render_bundle_summary(bundle_metadata, study_artifacts))

    return StudyBundleArtifacts(
        run_id=context.run_id,
        bundle_name=context.bundle_name,
        run_dir=run_dir,
        bundle_metadata_path=bundle_metadata_path,
        bundle_summary_path=bundle_summary_path,
        studies=study_artifacts,
        metadata=bundle_metadata,
    )


def _normalize_requested_studies(
    requested: list[str] | tuple[str, ...] | None,
) -> tuple[str, ...]:
    if not requested:
        return BASELINE_STUDY_NAMES

    ordered_unique = tuple(dict.fromkeys(requested))
    unknown = [study for study in ordered_unique if study not in _STUDY_EXECUTORS]
    if unknown:
        raise ValueError(
            "Unknown historical study names: "
            + ", ".join(sorted(unknown))
            + ". Available studies: "
            + ", ".join(AVAILABLE_STUDY_NAMES)
        )
    return ordered_unique


def _render_bundle_summary(
    metadata: dict[str, object],
    studies: tuple[StudyArtifacts, ...],
) -> str:
    lines = [
        "# Historical Study Bundle",
        "",
        f"- Bundle: `{metadata['bundle_name']}`",
        f"- Run id: `{metadata['run_id']}`",
        f"- Generated at: {metadata['generated_at']}",
        f"- Venue filter: `{metadata['venue_filter'] or 'all'}`",
        "",
        "## Studies",
        "",
    ]

    for study in studies:
        lines.append(f"- `{study.study_name}` (`{study.study_version}`)")
        lines.append(f"  metadata: `{study.metadata_path}`")
        lines.append(f"  summary: `{study.summary_path}`")
        lines.append(
            "  data: " + ", ".join(f"`{path}`" for path in study.data_paths)
        )

    return "\n".join(lines) + "\n"
