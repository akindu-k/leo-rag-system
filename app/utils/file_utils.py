import uuid
import mimetypes
from pathlib import Path
from typing import Optional


CONTENT_TYPE_MAP = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}


def get_file_extension(filename: str) -> str:
    return Path(filename).suffix.lstrip(".").lower()


def get_content_type(file_type: str) -> str:
    return CONTENT_TYPE_MAP.get(file_type, "application/octet-stream")


def build_storage_key(document_id: uuid.UUID, version_number: int, filename: str) -> str:
    """Build a deterministic MinIO object key."""
    ext = get_file_extension(filename)
    return f"documents/{document_id}/v{version_number}/{document_id}.{ext}"
