"""S3 upload/download utilities for ALIBI training scripts."""

import os
from pathlib import Path

import boto3


def _client():
    profile = os.environ.get("AWS_PROFILE", "alibi")
    session = boto3.Session(profile_name=profile)
    return session.client("s3")


def upload_file(local_path: str, bucket: str, key: str) -> None:
    local_path = Path(local_path)
    size_mb = local_path.stat().st_size / 1_048_576
    print(f"[s3] upload {local_path} → s3://{bucket}/{key} ({size_mb:.1f} MB)")
    _client().upload_file(str(local_path), bucket, key)


def download_file(bucket: str, key: str, local_path: str) -> None:
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[s3] download s3://{bucket}/{key} → {local_path}")
    _client().download_file(bucket, key, str(local_path))


def upload_dir(local_dir: str, bucket: str, prefix: str) -> None:
    """Upload all files in local_dir to s3://bucket/prefix/."""
    local_dir = Path(local_dir)
    for path in sorted(local_dir.rglob("*")):
        if path.is_file():
            key = f"{prefix}/{path.relative_to(local_dir).as_posix()}"
            upload_file(str(path), bucket, key)
