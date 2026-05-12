import os
import boto3
from botocore.client import Config

S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_PREFIX = "fintech-radar/projects"
PRESIGNED_URL_TTL = 3600


def get_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )


def upload_file(file_bytes: bytes, project_id: str, filename: str) -> tuple[str, int]:
    """Загружает файл в S3. Возвращает (s3_key, size)."""
    key = f"{S3_PREFIX}/{project_id}/{filename}"
    client = get_client()
    client.put_object(
        Bucket=S3_BUCKET,
        Key=key,
        Body=file_bytes,
    )
    return key, len(file_bytes)


def get_presigned_url(s3_key: str) -> str:
    client = get_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": s3_key},
        ExpiresIn=PRESIGNED_URL_TTL,
    )


def delete_file(s3_key: str) -> None:
    client = get_client()
    client.delete_object(Bucket=S3_BUCKET, Key=s3_key)
