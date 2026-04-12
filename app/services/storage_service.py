"""MinIO / S3 object storage service."""
import asyncio
import logging
import io
from functools import partial
from typing import BinaryIO

import boto3
from botocore.exceptions import ClientError
from botocore.config import Config

from app.core.config import settings

logger = logging.getLogger(__name__)

# Build a single reusable boto3 client (thread-safe for reads)
def _make_client():
    protocol = "https" if settings.STORAGE_USE_SSL else "http"
    return boto3.client(
        "s3",
        endpoint_url=f"{protocol}://{settings.STORAGE_ENDPOINT}",
        aws_access_key_id=settings.STORAGE_ACCESS_KEY,
        aws_secret_access_key=settings.STORAGE_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


_s3_client = None


def get_s3_client():
    global _s3_client
    if _s3_client is None:
        _s3_client = _make_client()
    return _s3_client


def _sync_init_bucket():
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=settings.STORAGE_BUCKET)
        logger.info(f"Bucket '{settings.STORAGE_BUCKET}' already exists.")
    except ClientError as e:
        if e.response["Error"]["Code"] == "404":
            client.create_bucket(Bucket=settings.STORAGE_BUCKET)
            logger.info(f"Bucket '{settings.STORAGE_BUCKET}' created.")
        else:
            raise


def _sync_upload(file_data: bytes, object_key: str, content_type: str) -> str:
    client = get_s3_client()
    client.put_object(
        Bucket=settings.STORAGE_BUCKET,
        Key=object_key,
        Body=file_data,
        ContentType=content_type,
    )
    return object_key


def _sync_download(object_key: str) -> bytes:
    client = get_s3_client()
    response = client.get_object(Bucket=settings.STORAGE_BUCKET, Key=object_key)
    return response["Body"].read()


def _sync_delete(object_key: str):
    client = get_s3_client()
    client.delete_object(Bucket=settings.STORAGE_BUCKET, Key=object_key)


async def init_storage():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _sync_init_bucket)


async def upload_file(file_data: bytes, object_key: str, content_type: str = "application/octet-stream") -> str:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_sync_upload, file_data, object_key, content_type))


async def download_file(object_key: str) -> bytes:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(_sync_download, object_key))


async def delete_file(object_key: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, partial(_sync_delete, object_key))
