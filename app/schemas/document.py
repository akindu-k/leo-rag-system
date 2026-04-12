from typing import Optional, List
import uuid
from datetime import datetime
from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    description: Optional[str]
    file_name: str
    file_type: str
    file_size: int
    uploaded_by: Optional[uuid.UUID]
    created_at: datetime
    latest_status: Optional[str] = None  # indexed status of latest version

    model_config = {"from_attributes": True}


class DocumentVersionOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    version_number: int
    status: str
    error_message: Optional[str]
    indexed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class IngestionJobOut(BaseModel):
    id: uuid.UUID
    document_version_id: uuid.UUID
    status: str
    error_message: Optional[str]
    chunks_processed: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentUploadResponse(BaseModel):
    document: DocumentOut
    version: DocumentVersionOut
    job: IngestionJobOut


class DocumentListResponse(BaseModel):
    items: List[DocumentOut]
    total: int
