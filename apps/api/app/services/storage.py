"""MinIO object storage service."""

from __future__ import annotations

import io

import urllib3
from minio import Minio
from minio.datatypes import Object as MinioObject
from minio.error import S3Error

from app.config import Settings

_MISSING_CODES = frozenset({"NoSuchKey", "NoSuchObject", "NotFound", "NoSuchBucket"})


class StorageService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,  # compose injects "minio:9000" (plain http)
        )

    @property
    def bucket(self) -> str:
        return self._settings.minio_bucket

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self.bucket):
            self._client.make_bucket(self.bucket)

    def put_bytes(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            self.bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def stat(self, key: str) -> MinioObject | None:
        try:
            return self._client.stat_object(self.bucket, key)
        except S3Error as exc:
            if exc.code in _MISSING_CODES:
                return None
            raise

    def open(
        self, key: str, offset: int = 0, length: int | None = None
    ) -> urllib3.response.BaseHTTPResponse:
        """Streaming GET. Caller must close/release the response."""
        return self._client.get_object(self.bucket, key, offset=offset, length=length)

    def get_bytes(self, key: str) -> bytes:
        response = self.open(key)
        try:
            return bytes(response.read())
        finally:
            response.close()
            response.release_conn()

    def download_to(self, key: str, dest_path: str) -> None:
        self._client.fget_object(self.bucket, key, dest_path)

    def remove(self, key: str) -> None:
        try:
            self._client.remove_object(self.bucket, key)
        except S3Error as exc:
            if exc.code not in _MISSING_CODES:
                raise
