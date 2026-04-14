from pathlib import Path

from services.research.bucket_sync import sync_artifact_bucket
from services.research.settings import ArtifactBucketSettings


def test_artifact_bucket_settings_accept_standard_aws_fallbacks():
    settings = ArtifactBucketSettings.from_env(
        {
            "ARTIFACT_BUCKET_ENABLED": "true",
            "BUCKET_NAME": "sleeperservice-artifacts",
            "AWS_ENDPOINT_URL": "https://bucket.internal",
            "AWS_REGION": "us-west-2",
            "AWS_ACCESS_KEY_ID": "access",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "ARTIFACT_BUCKET_PREFIX": "historical_research",
        }
    )

    assert settings.enabled is True
    assert settings.bucket_name == "sleeperservice-artifacts"
    assert settings.endpoint_url == "https://bucket.internal"
    assert settings.region == "us-west-2"
    assert settings.access_key_id == "access"
    assert settings.secret_access_key == "secret"
    assert settings.object_key("studies/latest/bundle_metadata.json") == (
        "historical_research/studies/latest/bundle_metadata.json"
    )


def test_sync_artifact_bucket_downloads_prefix_contents(tmp_path: Path):
    destination_root = tmp_path / "artifacts"
    client = _FakeS3Client(
        {
            "historical_research/studies/run-1/bundle_metadata.json": b'{"run_id":"run-1"}',
            "historical_research/studies/run-1/calibration/metadata.json": b"{}",
        }
    )
    settings = ArtifactBucketSettings(
        enabled=True,
        bucket_name="sleeperservice-artifacts",
        prefix="historical_research",
        endpoint_url=None,
        region=None,
        access_key_id=None,
        secret_access_key=None,
        session_token=None,
    )

    count = sync_artifact_bucket(
        bucket_settings=settings,
        destination_root=destination_root,
        client=client,
    )

    assert count == 2
    assert (destination_root / "studies" / "run-1" / "bundle_metadata.json").read_text() == (
        '{"run_id":"run-1"}'
    )
    assert (destination_root / "studies" / "run-1" / "calibration" / "metadata.json").exists()


class _FakePaginator:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def paginate(self, *, Bucket: str, Prefix: str):  # noqa: N803
        contents = [
            {"Key": key}
            for key in sorted(self.objects)
            if key.startswith(Prefix)
        ]
        yield {"Contents": contents}


class _FakeS3Client:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def get_paginator(self, name: str):
        assert name == "list_objects_v2"
        return _FakePaginator(self.objects)

    def download_file(self, bucket: str, key: str, filename: str):
        assert bucket == "sleeperservice-artifacts"
        Path(filename).write_bytes(self.objects[key])
