# manuals/r2_multipart.py

from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone
from django.utils.text import get_valid_filename


ALLOWED_UPLOAD_EXTENSIONS = {
    ".pdf",
    ".zip",
}


@lru_cache(maxsize=1)
def get_r2_client():
    """
    Cloudflare R2용 S3 호환 클라이언트를 반환합니다.
    """

    return boto3.client(
        "s3",
        endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            s3={
                "addressing_style": "path",
            },
            retries={
                "max_attempts": 5,
                "mode": "standard",
            },
        ),
    )


def normalize_filename(filename):
    """
    경로 조작을 방지하고 저장 가능한 안전한 파일명으로 변환합니다.
    """

    original_name = Path(filename or "").name
    safe_name = get_valid_filename(original_name)

    if not safe_name:
        safe_name = f"upload-{uuid4().hex}"

    return safe_name


def validate_upload_file(filename, file_size):
    """
    파일명, 확장자, 파일 크기를 검사합니다.
    """

    safe_name = normalize_filename(filename)
    extension = Path(safe_name).suffix.lower()

    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError("PDF 또는 ZIP 파일만 업로드할 수 있습니다.")

    try:
        normalized_size = int(file_size)
    except (TypeError, ValueError):
        raise ValueError("올바른 파일 크기가 아닙니다.")

    if normalized_size <= 0:
        raise ValueError("빈 파일은 업로드할 수 없습니다.")

    if normalized_size > settings.AWS_MAX_UPLOAD_SIZE:
        max_size_gb = settings.AWS_MAX_UPLOAD_SIZE / (1024**3)

        raise ValueError(
            f"파일은 최대 {max_size_gb:g}GB까지 업로드할 수 있습니다."
        )

    return safe_name, normalized_size, extension


def get_content_type(extension):
    content_type_map = {
        ".pdf": "application/pdf",
        ".zip": "application/zip",
    }

    return content_type_map.get(
        extension.lower(),
        "application/octet-stream",
    )


def build_object_key(filename, upload_type, aircraft_id=None, manual_type=None):
    """
    R2 내부에 저장할 객체 key를 생성합니다.

    예:
    manuals/packages/3/AMM/2026/07/uuid-manual.zip
    manuals/files/3/MEL/2026/07/uuid-manual.pdf
    """

    now = timezone.now()
    safe_name = normalize_filename(filename)

    allowed_upload_types = {
        "package": "packages",
        "file": "files",
    }

    directory = allowed_upload_types.get(upload_type)

    if not directory:
        raise ValueError("올바른 업로드 종류가 아닙니다.")

    aircraft_segment = str(aircraft_id or "unassigned")
    manual_segment = get_valid_filename(
        str(manual_type or "OTHER").upper()
    )

    unique_name = f"{uuid4().hex}-{safe_name}"

    return (
        f"manuals/{directory}/"
        f"{aircraft_segment}/"
        f"{manual_segment}/"
        f"{now:%Y/%m}/"
        f"{unique_name}"
    )


def create_multipart_upload(object_key, content_type):
    """
    R2 Multipart Upload를 시작하고 upload_id를 반환합니다.
    """

    client = get_r2_client()

    response = client.create_multipart_upload(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=object_key,
        ContentType=content_type,
    )

    return response["UploadId"]


def generate_upload_part_url(
    object_key,
    upload_id,
    part_number,
):
    """
    브라우저가 해당 파트를 R2에 직접 올릴 수 있도록
    Presigned URL을 생성합니다.
    """

    client = get_r2_client()

    return client.generate_presigned_url(
        ClientMethod="upload_part",
        Params={
            "Bucket": settings.AWS_STORAGE_BUCKET_NAME,
            "Key": object_key,
            "UploadId": upload_id,
            "PartNumber": int(part_number),
        },
        ExpiresIn=settings.AWS_PRESIGNED_URL_EXPIRES,
        HttpMethod="PUT",
    )


def complete_multipart_upload(
    object_key,
    upload_id,
    parts,
):
    """
    모든 파트 업로드를 완료하고 하나의 R2 객체로 결합합니다.
    """

    client = get_r2_client()

    normalized_parts = sorted(
        [
            {
                "ETag": part["etag"],
                "PartNumber": int(part["part_number"]),
            }
            for part in parts
        ],
        key=lambda item: item["PartNumber"],
    )

    return client.complete_multipart_upload(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=object_key,
        UploadId=upload_id,
        MultipartUpload={
            "Parts": normalized_parts,
        },
    )


def abort_multipart_upload(object_key, upload_id):
    """
    실패하거나 취소된 Multipart Upload를 제거합니다.
    """

    client = get_r2_client()

    client.abort_multipart_upload(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=object_key,
        UploadId=upload_id,
    )


def object_exists(object_key):
    """
    완료 처리 전에 R2 객체가 실제 존재하는지 확인합니다.
    """

    client = get_r2_client()

    try:
        response = client.head_object(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=object_key,
        )
    except ClientError:
        return False, None

    return True, response


def delete_object(object_key):
    """
    DB 저장 실패 시 이미 업로드된 객체를 삭제할 때 사용합니다.
    """

    client = get_r2_client()

    client.delete_object(
        Bucket=settings.AWS_STORAGE_BUCKET_NAME,
        Key=object_key,
    )
