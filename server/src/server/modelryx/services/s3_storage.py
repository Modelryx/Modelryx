"""S3-compatible object operations. Application workflows still use local storage."""
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


class S3ObjectStorage:
    """Use an existing private bucket; credentials follow the AWS credential chain."""

    def __init__(
        self, bucket: str, *, endpoint_url: str | None = None,
        region_name: str | None = None, prefix: str = "", addressing_style: str = "auto",
        client=None,
    ) -> None:
        if not bucket.strip():
            raise ValueError("An S3 bucket is required")
        if addressing_style not in {"auto", "path", "virtual"}:
            raise ValueError("Invalid S3 addressing style")
        self.bucket = bucket
        self.prefix = self._validate_key(prefix.rstrip("/")) + "/" if prefix else ""
        self.client = client if client is not None else boto3.client(
            "s3", endpoint_url=endpoint_url, region_name=region_name,
            config=Config(
                signature_version="s3v4", s3={"addressing_style": addressing_style},
                connect_timeout=10, read_timeout=60,
                retries={"mode": "standard", "max_attempts": 3},
            ),
        )

    @staticmethod
    def _validate_key(key: str) -> str:
        if (
            not key or "\\" in key or any(ord(char) < 32 for char in key)
            or any(part in {"", ".", ".."} for part in key.split("/"))
        ):
            raise ValueError(
                "Object keys must be relative paths without empty or traversal segments"
            )
        return key

    def _key(self, storage_key: str) -> str:
        key = self.prefix + self._validate_key(storage_key)
        if len(key.encode("utf-8")) > 1024:
            raise ValueError("S3 object key exceeds 1024 bytes")
        return key

    def upload(self, storage_key: str, source: BinaryIO) -> None:
        """Upload from the stream's current position, using managed multipart transfers."""
        self.client.upload_fileobj(source, self.bucket, self._key(storage_key))

    def upload_file(self, storage_key: str, source: Path) -> None:
        with source.open("rb") as stream:
            self.upload(storage_key, stream)

    def open_download(self, storage_key: str) -> BinaryIO:
        """Return a streaming body. The caller must close it."""
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(storage_key))["Body"]
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
                raise FileNotFoundError(storage_key) from exc
            raise

    def download_file(self, storage_key: str, destination: Path) -> None:
        """Publish a complete local copy atomically; remove temporary files on failure."""
        import os
        import tempfile

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as target:
                temporary = Path(target.name)
                with self.open_download(storage_key) as source:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def exists(self, storage_key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(storage_key))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
                return False
            raise
        return True

    def delete(self, storage_key: str) -> None:
        """Delete the current object; versioned buckets retain historical versions."""
        self.client.delete_object(Bucket=self.bucket, Key=self._key(storage_key))
