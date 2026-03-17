import json

import pytest

from services.research.dataset_profile import discover_dataset_files, profile_dataset
from services.research.settings import HistoricalResearchSettings


def test_historical_research_settings_resolve_relative_paths(tmp_path, monkeypatch):
    dataset_root = tmp_path / "dataset"
    output_root = tmp_path / "artifacts"
    dataset_root.mkdir()

    monkeypatch.chdir(tmp_path)
    settings = HistoricalResearchSettings.from_env(
        {
            "HISTORICAL_DATASET_ROOT": "dataset",
            "HISTORICAL_RESEARCH_OUTPUT_ROOT": "artifacts",
        }
    )

    assert settings.dataset_root == dataset_root.resolve()
    assert settings.output_root == output_root.resolve()


def test_historical_research_settings_read_process_environment(tmp_path, monkeypatch):
    dataset_root = tmp_path / "dataset"
    output_root = tmp_path / "artifacts"
    dataset_root.mkdir()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HISTORICAL_DATASET_ROOT", "dataset")
    monkeypatch.setenv("HISTORICAL_RESEARCH_OUTPUT_ROOT", "artifacts")

    settings = HistoricalResearchSettings.from_env()

    assert settings.dataset_root == dataset_root.resolve()
    assert settings.output_root == output_root.resolve()


def test_discover_dataset_files_guesses_venues_and_skips_hidden_paths(tmp_path):
    dataset_root = tmp_path / "dataset"
    (dataset_root / "polymarket").mkdir(parents=True)
    (dataset_root / "kalshi").mkdir(parents=True)
    (dataset_root / ".hidden").mkdir(parents=True)

    (dataset_root / "polymarket" / "trades.parquet").write_text("a", encoding="utf-8")
    (dataset_root / "polymarket" / "._trades.parquet").write_text("sidecar", encoding="utf-8")
    (dataset_root / "kalshi" / "markets.csv").write_text("b", encoding="utf-8")
    (dataset_root / ".hidden" / "ignored.parquet").write_text("c", encoding="utf-8")

    files = discover_dataset_files(dataset_root)

    assert [file.relative_path for file in files] == [
        "kalshi/markets.csv",
        "polymarket/trades.parquet",
    ]
    assert [file.guessed_venue for file in files] == ["kalshi", "polymarket"]


def test_profile_dataset_writes_manifest_and_summary_without_duckdb(tmp_path):
    dataset_root = tmp_path / "dataset"
    output_root = tmp_path / "outputs"
    (dataset_root / "polymarket").mkdir(parents=True)
    (dataset_root / "kalshi").mkdir(parents=True)

    (dataset_root / "polymarket" / "trades.parquet").write_text("not parquet", encoding="utf-8")
    (dataset_root / "kalshi" / "markets.json").write_text("{}", encoding="utf-8")

    artifacts = profile_dataset(
        HistoricalResearchSettings(
            dataset_root=dataset_root,
            output_root=output_root,
        )
    )

    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    summary = artifacts.summary_path.read_text(encoding="utf-8")

    assert manifest["inventory"]["total_files"] == 2
    assert manifest["inventory"]["venues"] == {"kalshi": 1, "polymarket": 1}
    assert manifest["file_record_strategy"] == "full"
    assert len(manifest["parquet_collections"]) == 1
    assert manifest["capabilities"]["duckdb_available"] is True
    assert manifest["capabilities"]["parquet_audit_performed"] is True
    assert any("parquet profiling failed" in warning.lower() for warning in manifest["warnings"])
    assert manifest["parquet_audit"]["collection_count"] == 1
    assert manifest["parquet_audit"]["collections"][0]["error"]
    assert "Historical Dataset Profile" in summary
    assert "Parquet audit performed: True" in summary
    assert "Parquet collections discovered: 1" in summary


def test_profile_historical_dataset_help_does_not_require_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("database_url", raising=False)

    from services.tools import profile_historical_dataset

    with pytest.raises(SystemExit) as exc_info:
        profile_historical_dataset.main(["--help"])

    assert exc_info.value.code == 0
    help_output = capsys.readouterr().out
    assert "HISTORICAL_RESEARCH_OUTPUT_ROOT" in help_output
