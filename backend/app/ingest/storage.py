"""The object store: where Parquet tables and raw uploads live.

One interface, two implementations, chosen by `ALIBI_OBJECT_STORE`:

* `LocalFileStore` — files under `backend/.data/`. The default, so the whole
  ingestion flow runs on a laptop with no AWS account, no credentials, and no
  network. This is not a test double; it is a real backend that the local dev
  server uses.
* `S3Store` — the deployed one.

Two details make the two interchangeable rather than merely similar:

* **`uri_for()`** returns something DuckDB can read directly — a filesystem
  path or an `s3://` URL — so the query layer has no branch in it at all.
* **Presigned URLs.** S3 signs them offline (SigV4 is pure computation, no API
  call), which is why the API Lambda can issue one without network access to
  S3. Locally there is nothing to sign, so the store returns a URL pointing at
  a backend endpoint that accepts the bytes — same shape, same client code.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import ObjectStoreKind, get_settings

# How long an upload URL stays valid. Long enough for a slow connection to
# finish a 100 MB file (Pro's limit) at ~1 Mbps, short enough that a leaked URL is
# not a standing hole.
UPLOAD_URL_TTL_SECONDS = 900


class ObjectStore(ABC):
    """Content-addressed by key; keys are `/`-separated and tenant-prefixed."""

    @abstractmethod
    def put(self, key: str, data: bytes, content_type: str | None = None) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> int: ...

    @abstractmethod
    def list_prefix(self, prefix: str) -> list[str]:
        """Keys under `prefix`. Used to recover a raw upload's filename on retry,
        where the only thing the caller still has is the ids."""

    @abstractmethod
    def uri_for(self, key: str) -> str:
        """A locator DuckDB can read directly."""

    @abstractmethod
    def presigned_put(
        self, key: str, content_type: str | None = None, ttl_seconds: int = UPLOAD_URL_TTL_SECONDS
    ) -> tuple[str, dict[str, str], datetime]:
        """(url, headers to send, expiry) for a direct browser upload."""


class LocalFileStore(ObjectStore):
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Resolve and confine: a key is user-influenced (it carries a filename),
        # so `../` must not be able to escape the data directory.
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError(f"key escapes the store root: {key!r}")
        return target

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> int:
        base = self._path(prefix)
        if not base.exists():
            return 0
        count = sum(1 for p in base.rglob("*") if p.is_file())
        shutil.rmtree(base, ignore_errors=True)
        return count

    def list_prefix(self, prefix: str) -> list[str]:
        base = self._path(prefix)
        if not base.exists():
            return []
        return sorted(
            p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file()
        )

    def uri_for(self, key: str) -> str:
        return str(self._path(key))

    def presigned_put(
        self, key: str, content_type: str | None = None, ttl_seconds: int = UPLOAD_URL_TTL_SECONDS
    ) -> tuple[str, dict[str, str], datetime]:
        # No signing locally: the API exposes a dev-only receiver at this path
        # that writes straight into the store, so the frontend's upload code
        # is identical in both modes.
        expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        headers = {"Content-Type": content_type} if content_type else {}
        return f"/api/v1/uploads/local/{key}", headers, expires


class S3Store(ObjectStore):
    def __init__(self, bucket: str, region: str) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.region = region
        # botocore's default signer for the "us-east-1" / global s3.amazonaws.com
        # endpoint is legacy SigV2 (AWSAccessKeyId=...&Signature=...), which AWS
        # now rejects outright (403) for buckets like this one — SigV2 has been
        # retired. Force SigV4 explicitly rather than relying on the default.
        self._client = boto3.client("s3", region_name=region, config=Config(signature_version="s3v4"))

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        extra = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)

    def get(self, key: str) -> bytes:
        return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def delete_prefix(self, prefix: str) -> int:
        paginator = self._client.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})
                deleted += len(keys)
        return deleted

    def list_prefix(self, prefix: str) -> list[str]:
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(o["Key"] for o in page.get("Contents", []))
        return keys

    def uri_for(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def presigned_put(
        self, key: str, content_type: str | None = None, ttl_seconds: int = UPLOAD_URL_TTL_SECONDS
    ) -> tuple[str, dict[str, str], datetime]:
        params: dict[str, str] = {"Bucket": self.bucket, "Key": key}
        headers: dict[str, str] = {}
        if content_type:
            params["ContentType"] = content_type
            # A signed ContentType is part of the signature, so the browser
            # MUST send back exactly this header or S3 rejects the PUT.
            headers["Content-Type"] = content_type
        url = self._client.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=ttl_seconds
        )
        return url, headers, datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)


_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    """Process-wide store, built from settings on first use."""
    global _store
    if _store is None:
        settings = get_settings()
        if settings.object_store is ObjectStoreKind.s3:
            if not settings.ingest_bucket:
                raise RuntimeError(
                    "ALIBI_OBJECT_STORE=s3 requires ALIBI_INGEST_BUCKET to be set"
                )
            _store = S3Store(settings.ingest_bucket, settings.aws_region)
        else:
            _store = LocalFileStore(settings.ingest_local_dir)
    return _store


def reset_object_store() -> None:
    """Drop the cached store — used by tests that switch backends."""
    global _store
    _store = None


# ---------------------------------------------------------------------------
# Key layout
#
# Every key is prefixed with the owner's id. That is defence in depth, not the
# access control itself (which is the owner_id check on every query) — but it
# means a bug that leaks a key still can't produce a path into another user's
# data by accident, and it makes `delete_prefix` per user trivial.
# ---------------------------------------------------------------------------


# The single place the raw-upload layout is written down. The stage-1 Lambda
# parses this shape back out of an S3 event key (`lambda_handler.parse_raw_key`)
# because it has no database to look the ids up in — so the two must agree.
RAW_UPLOAD_ROOT = "uploads"


def raw_upload_prefix(user_id: str, upload_id: str) -> str:
    return f"{RAW_UPLOAD_ROOT}/{user_id}/{upload_id}"


def raw_upload_key(user_id: str, upload_id: str, filename: str) -> str:
    safe = filename.replace("/", "_").replace("\\", "_").lstrip(".")
    return f"{raw_upload_prefix(user_id, upload_id)}/{safe or 'archivo'}"


def dataset_parquet_key(user_id: str, dataset_id: str) -> str:
    return f"tenants/{user_id}/datasets/{dataset_id}/data.parquet"


def dataset_profile_key(user_id: str, dataset_id: str) -> str:
    return f"tenants/{user_id}/datasets/{dataset_id}/profile.json"


def upload_status_key(user_id: str, upload_id: str) -> str:
    return f"tenants/{user_id}/uploads/{upload_id}/status.json"


def upload_result_key(user_id: str, upload_id: str) -> str:
    return f"tenants/{user_id}/uploads/{upload_id}/result.json"


def dataset_prefix(user_id: str, dataset_id: str) -> str:
    return f"tenants/{user_id}/datasets/{dataset_id}"


__all__ = [
    "RAW_UPLOAD_ROOT",
    "UPLOAD_URL_TTL_SECONDS",
    "LocalFileStore",
    "ObjectStore",
    "S3Store",
    "dataset_parquet_key",
    "dataset_prefix",
    "dataset_profile_key",
    "get_object_store",
    "raw_upload_key",
    "raw_upload_prefix",
    "reset_object_store",
    "upload_result_key",
    "upload_status_key",
]
