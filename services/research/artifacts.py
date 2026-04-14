"""Load promoted historical-study artifacts for API and downstream consumers."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from services.research.settings import HistoricalResearchSettings


@dataclass(frozen=True)
class StudyBundleReference:
    """Resolved reference to one saved study bundle."""

    output_root: Path
    run_id: str
    run_dir: Path
    bundle_metadata_path: Path
    bundle_metadata: dict[str, Any]


class HistoricalArtifactStore:
    """Read promoted historical-study outputs without touching raw datasets."""

    def __init__(self, output_root: Path):
        self.output_root = output_root.expanduser().resolve()
        self._bundle_cache: StudyBundleReference | None = None

    @classmethod
    def from_env(cls) -> "HistoricalArtifactStore":
        settings = HistoricalResearchSettings.from_env()
        return cls(output_root=settings.output_root)

    def latest_bundle(self) -> StudyBundleReference | None:
        if self._bundle_cache is not None:
            return self._bundle_cache

        studies_root = self.output_root / "studies"
        if not studies_root.exists():
            return None

        candidates = sorted(
            (
                run_dir / "bundle_metadata.json"
                for run_dir in studies_root.iterdir()
                if run_dir.is_dir()
            ),
            reverse=True,
        )
        for metadata_path in candidates:
            metadata = _load_json(metadata_path)
            if metadata is None:
                continue
            run_dir = metadata_path.parent
            reference = StudyBundleReference(
                output_root=self.output_root,
                run_id=str(metadata.get("run_id") or run_dir.name),
                run_dir=run_dir,
                bundle_metadata_path=metadata_path,
                bundle_metadata=metadata,
            )
            self._bundle_cache = reference
            return reference
        return None

    def bundle_provenance(self) -> dict[str, Any] | None:
        bundle = self.latest_bundle()
        if bundle is None:
            return None
        return {
            "run_id": bundle.run_id,
            "bundle_metadata_path": str(bundle.bundle_metadata_path),
            "generated_at": bundle.bundle_metadata.get("generated_at"),
            "study_names": bundle.bundle_metadata.get("study_names", []),
        }

    def lookup_calibration_context(
        self,
        *,
        venue: str,
        price_bucket: str,
        time_to_resolution_bucket: str,
    ) -> dict[str, Any] | None:
        path = self._study_parquet_path("calibration", "calibration_surface.parquet")
        if path is None:
            return None
        row = _fetch_one(
            path,
            """
            SELECT
                venue,
                price_bucket,
                time_to_resolution_bucket,
                trade_count,
                market_count,
                avg_implied_probability,
                realized_win_rate,
                avg_miscalibration,
                mean_squared_error
            FROM read_parquet(?)
            WHERE venue = ?
              AND price_bucket = ?
              AND time_to_resolution_bucket = ?
            LIMIT 1
            """,
            [str(path), venue, price_bucket, time_to_resolution_bucket],
        )
        if row is None:
            return None
        return {
            "venue": row["venue"],
            "price_bucket": row["price_bucket"],
            "time_to_resolution_bucket": row["time_to_resolution_bucket"],
            "historical_trade_count": row["trade_count"],
            "historical_market_count": row["market_count"],
            "avg_implied_probability": row["avg_implied_probability"],
            "realized_win_rate": row["realized_win_rate"],
            "avg_miscalibration": row["avg_miscalibration"],
            "mean_squared_error": row["mean_squared_error"],
        }

    def lookup_maker_taker_context(
        self,
        *,
        venue: str,
        price_bucket: str,
        time_to_resolution_bucket: str,
        role_basis: str = "counterparty_inferred",
    ) -> dict[str, Any] | None:
        path = self._study_parquet_path("maker_taker_expectancy", "expectancy_surface.parquet")
        if path is None:
            return None
        rows = _fetch_all(
            path,
            """
            SELECT
                maker_taker_role,
                trade_count,
                avg_pnl_per_contract
            FROM read_parquet(?)
            WHERE role_basis = ?
              AND venue = ?
              AND price_bucket = ?
              AND time_to_resolution_bucket = ?
            ORDER BY maker_taker_role
            """,
            [str(path), role_basis, venue, price_bucket, time_to_resolution_bucket],
        )
        if not rows:
            return None

        maker_row = next((row for row in rows if row["maker_taker_role"] == "maker"), None)
        taker_row = next((row for row in rows if row["maker_taker_role"] == "taker"), None)
        if maker_row is None and taker_row is None:
            return None

        summary_parts: list[str] = []
        if maker_row is not None and taker_row is not None:
            if maker_row["avg_pnl_per_contract"] > taker_row["avg_pnl_per_contract"]:
                summary_parts.append(
                    "Passive participation historically outperformed taking in this bucket."
                )
            elif taker_row["avg_pnl_per_contract"] > maker_row["avg_pnl_per_contract"]:
                summary_parts.append(
                    "Taking historically outperformed passive participation in this bucket."
                )
            else:
                summary_parts.append(
                    "Maker and taker expectancy were historically similar in this bucket."
                )
        else:
            summary_parts.append("Only one side of the maker/taker comparison is available in this bucket.")

        return {
            "role_basis": role_basis,
            "maker_avg_pnl_per_contract": maker_row["avg_pnl_per_contract"] if maker_row else None,
            "taker_avg_pnl_per_contract": taker_row["avg_pnl_per_contract"] if taker_row else None,
            "maker_trade_count": maker_row["trade_count"] if maker_row else None,
            "taker_trade_count": taker_row["trade_count"] if taker_row else None,
            "summary": " ".join(summary_parts),
        }

    def lookup_sizing_context(
        self,
        *,
        venue: str,
        price_bucket: str,
        time_to_resolution_bucket: str,
        role_basis: str = "counterparty_inferred",
        maker_taker_role: str = "taker",
    ) -> dict[str, Any] | None:
        path = self._study_parquet_path("sizing_priors", "sizing_priors.parquet")
        if path is None:
            return None
        row = _fetch_one(
            path,
            """
            SELECT
                trade_count,
                edge_to_noise_ratio,
                recommended_haircut_multiplier,
                promotion_status
            FROM read_parquet(?)
            WHERE role_basis = ?
              AND venue = ?
              AND maker_taker_role = ?
              AND price_bucket = ?
              AND time_to_resolution_bucket = ?
            LIMIT 1
            """,
            [
                str(path),
                role_basis,
                venue,
                maker_taker_role,
                price_bucket,
                time_to_resolution_bucket,
            ],
        )
        if row is None:
            return None

        status = row["promotion_status"]
        if status == "candidate":
            summary = "Historical edge is positive and the bucket is candidate-grade."
        elif status == "candidate_with_haircut":
            summary = "Historical edge exists, but dispersion warrants a haircut."
        elif status == "experimental_sparse":
            summary = "The bucket is still sparse and should remain experimental."
        else:
            summary = "Historical edge is not strong enough for promotion."

        return {
            "role_basis": role_basis,
            "maker_taker_role": maker_taker_role,
            "recommended_haircut_multiplier": row["recommended_haircut_multiplier"],
            "promotion_status": status,
            "edge_to_noise_ratio": row["edge_to_noise_ratio"],
            "trade_count": row["trade_count"],
            "summary": summary,
        }

    def _study_parquet_path(self, study_name: str, basename: str) -> Path | None:
        bundle = self.latest_bundle()
        if bundle is None:
            return None
        outputs = bundle.bundle_metadata.get("study_outputs", {})
        study_output = outputs.get(study_name)
        if not isinstance(study_output, dict):
            return None

        data_paths = study_output.get("data_paths", [])
        for raw_path in data_paths:
            path = Path(str(raw_path))
            if path.name == basename:
                return path
        return None


def _fetch_one(path: Path, query: str, params: list[Any]) -> dict[str, Any] | None:
    rows = _fetch_all(path, query, params)
    return rows[0] if rows else None


def _fetch_all(path: Path, query: str, params: list[Any]) -> list[dict[str, Any]]:
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "duckdb is required to load promoted historical-study artifacts."
        ) from exc

    connection = duckdb.connect(database=":memory:")
    try:
        result = connection.execute(query, params)
        column_names = [column[0] for column in result.description]
        rows = []
        for raw_row in result.fetchall():
            rows.append(
                {
                    column_names[index]: _coerce_scalar(value)
                    for index, value in enumerate(raw_row)
                }
            )
        return rows
    finally:
        connection.close()


def _coerce_scalar(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # pragma: no cover - defensive scalar fallback
            return value
    return value


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
