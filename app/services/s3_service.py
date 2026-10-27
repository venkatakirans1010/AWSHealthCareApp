import io
import json
from typing import Any, BinaryIO
from functools import lru_cache

import boto3
from botocore.config import Config

from app.config import AWS_REGION, S3_BUCKET_NAME


class S3ConfigurationError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _get_s3_client():
    """Create one reusable client lazily so API startup does not require AWS credentials."""
    return boto3.client(
        "s3",
        region_name=AWS_REGION,
        config=Config(retries={"total_max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=30),
    )


def upload_claim(file: BinaryIO, claim_id: str, filename: str) -> str:
    if not S3_BUCKET_NAME:
        raise S3ConfigurationError("S3_BUCKET_NAME is not configured")
    object_key = f"incoming/{claim_id}/{filename}"
    _get_s3_client().upload_fileobj(
        file,
        S3_BUCKET_NAME,
        object_key,
        ExtraArgs={"ContentType": "application/pdf", "ServerSideEncryption": "AES256"},
    )
    return object_key


def download_bytes(object_key: str) -> bytes:
    """Download a configured-bucket object into memory."""
    if not S3_BUCKET_NAME:
        raise S3ConfigurationError("S3_BUCKET_NAME is not configured")
    buffer = io.BytesIO()
    _get_s3_client().download_fileobj(S3_BUCKET_NAME, object_key, buffer)
    return buffer.getvalue()


def upload_json(payload: dict[str, Any], object_key: str) -> str:
    """Upload a private, encrypted JSON artifact to the configured bucket."""
    if not S3_BUCKET_NAME:
        raise S3ConfigurationError("S3_BUCKET_NAME is not configured")
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    _get_s3_client().upload_fileobj(
        io.BytesIO(body),
        S3_BUCKET_NAME,
        object_key,
        ExtraArgs={"ContentType": "application/json", "ServerSideEncryption": "AES256"},
    )
    return object_key


def download_json(object_key: str) -> tuple[dict[str, Any], str]:
    """Load a JSON object and return its payload and stable S3 ETag."""
    if not S3_BUCKET_NAME:
        raise S3ConfigurationError("S3_BUCKET_NAME is not configured")
    response = _get_s3_client().get_object(Bucket=S3_BUCKET_NAME, Key=object_key)
    with response["Body"] as body:
        content = body.read()
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("S3 JSON artifact must contain an object at its root")
    etag = str(response.get("ETag", "")).strip('"')
    if not etag:
        raise ValueError("S3 JSON artifact response did not include an ETag")
    return payload, etag
