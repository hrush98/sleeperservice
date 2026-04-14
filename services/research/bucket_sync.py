"""Sync promoted historical artifacts from S3-compatible object storage."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from services.research.settings import ArtifactBucketSettings, HistoricalResearchSettings

logger = logging.getLogger(__name__)


def sync_artifact_bucket(
    *,
    bucket_settings: ArtifactBucketSettings,
    destination_root: Path,
    client: Any | None = None,
) -> int:
    """Download all objects for the configured prefix into the destination root."""

    if not bucket_settings.enabled:
        return 0
    if not bucket_settings.bucket_name:
        raise ValueError("ARTIFACT_BUCKET_ENABLED is true but no bucket name is configured.")

    s3_client = client or _build_s3_client(bucket_settings)
    paginator = s3_client.get_paginator("list_objects_v2")
    download_count = 0
    destination_root.mkdir(parents=True, exist_ok=True)

    prefix = f"{bucket_settings.prefix}/" if bucket_settings.prefix else ""
    for page in paginator.paginate(Bucket=bucket_settings.bucket_name, Prefix=prefix):
        for item in page.get("Contents", []):
            key = str(item.get("Key") or "")
            if not key or key.endswith("/"):
                continue
            relative_key = key[len(prefix):] if prefix and key.startswith(prefix) else key
            target_path = destination_root / relative_key
            target_path.parent.mkdir(parents=True, exist_ok=True)
            s3_client.download_file(bucket_settings.bucket_name, key, str(target_path))
            download_count += 1

    logger.info(
        "Synced %d artifact files from bucket '%s' prefix '%s' into '%s'.",
        download_count,
        bucket_settings.bucket_name,
        bucket_settings.prefix,
        destination_root,
    )
    return download_count


def sync_artifact_bucket_from_env() -> int:
    """Sync promoted artifacts when the bucket integration is enabled."""

    bucket_settings = ArtifactBucketSettings.from_env()
    if not bucket_settings.enabled:
        return 0
    research_settings = HistoricalResearchSettings.from_env()
    return sync_artifact_bucket(
        bucket_settings=bucket_settings,
        destination_root=research_settings.output_root,
    )


def _build_s3_client(bucket_settings: ArtifactBucketSettings):
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required for artifact bucket sync.") from exc

    session = boto3.session.Session()
    return session.client(
        "s3",
        endpoint_url=bucket_settings.endpoint_url,
        region_name=bucket_settings.region,
        aws_access_key_id=bucket_settings.access_key_id,
        aws_secret_access_key=bucket_settings.secret_access_key,
        aws_session_token=bucket_settings.session_token,
    )
