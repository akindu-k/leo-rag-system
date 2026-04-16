"""Document upload and management endpoints."""
import uuid
import logging
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.database import get_db
from app.core.security import get_current_user, require_admin
from app.core.config import settings
from app.models.user import User
from app.models.document import Document, DocumentVersion, DocumentAccessRule, IngestionJob
from app.schemas.document import (
    DocumentOut,
    DocumentVersionOut,
    DocumentUploadResponse,
    DocumentListResponse,
    IngestionJobOut,
)
from app.services import storage_service
from app.services.ingestion_service import run_ingestion
from app.utils.file_utils import get_file_extension, get_content_type, build_storage_key

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)


@router.post("", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a document and start background ingestion."""
    # ── Validate file type ────────────────────────────────────────────────
    ext = get_file_extension(file.filename or "")
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File type '.{ext}' not supported. Allowed: {settings.ALLOWED_EXTENSIONS}",
        )

    file_bytes = await file.read()

    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {settings.MAX_UPLOAD_SIZE_MB} MB limit.",
        )

    doc_id = uuid.uuid4()
    version_number = 1
    doc_title = (title or file.filename or "Untitled").strip()

    # ── Upload to MinIO ───────────────────────────────────────────────────
    storage_key = build_storage_key(doc_id, version_number, file.filename or "file")
    await storage_service.upload_file(file_bytes, storage_key, get_content_type(ext))

    # ── Persist records ───────────────────────────────────────────────────
    doc = Document(
        id=doc_id,
        title=doc_title,
        description=description,
        file_name=file.filename or "file",
        file_type=ext,
        file_size=len(file_bytes),
        storage_path=storage_key,
        uploaded_by=current_user.id,
    )
    db.add(doc)
    await db.flush()

    version = DocumentVersion(
        id=uuid.uuid4(),
        document_id=doc.id,
        version_number=version_number,
        storage_path=storage_key,
        status="pending",
    )
    db.add(version)

    # By default: all authenticated users can access this document
    db.add(DocumentAccessRule(id=uuid.uuid4(), document_id=doc.id, subject_type="all"))

    job = IngestionJob(id=uuid.uuid4(), document_version_id=version.id, status="pending")
    db.add(job)

    await db.flush()
    await db.refresh(doc)
    await db.refresh(version)
    await db.refresh(job)

    # Commit NOW so the background task can find these records in a fresh session
    await db.commit()

    # ── Queue background ingestion ────────────────────────────────────────
    background_tasks.add_task(run_ingestion, job.id)
    logger.info(f"Document '{doc.title}' uploaded. Ingestion job {job.id} queued.")

    return DocumentUploadResponse(
        document=DocumentOut(
            id=doc.id,
            title=doc.title,
            description=doc.description,
            file_name=doc.file_name,
            file_type=doc.file_type,
            file_size=doc.file_size,
            uploaded_by=doc.uploaded_by,
            created_at=doc.created_at,
            latest_status=version.status,
        ),
        version=DocumentVersionOut.model_validate(version),
        job=IngestionJobOut.model_validate(job),
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all non-deleted documents with their latest ingestion status."""
    docs_result = await db.execute(
        select(Document)
        .where(Document.is_deleted == False)  # noqa: E712
        .order_by(Document.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    docs = docs_result.scalars().all()

    count_result = await db.execute(
        select(func.count()).select_from(Document).where(Document.is_deleted == False)  # noqa: E712
    )
    total = count_result.scalar()

    items = []
    for doc in docs:
        ver_result = await db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == doc.id)
            .order_by(DocumentVersion.version_number.desc())
            .limit(1)
        )
        latest = ver_result.scalar_one_or_none()
        items.append(
            DocumentOut(
                id=doc.id,
                title=doc.title,
                description=doc.description,
                file_name=doc.file_name,
                file_type=doc.file_type,
                file_size=doc.file_size,
                uploaded_by=doc.uploaded_by,
                created_at=doc.created_at,
                latest_status=latest.status if latest else None,
            )
        )

    return DocumentListResponse(items=items, total=total)


@router.get("/{document_id}/jobs", response_model=List[IngestionJobOut])
async def get_ingestion_jobs(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = await db.get(Document, document_id)
    if not doc or doc.is_deleted:
        raise HTTPException(status_code=404, detail="Document not found")

    result = await db.execute(
        select(IngestionJob)
        .join(DocumentVersion, DocumentVersion.id == IngestionJob.document_version_id)
        .where(DocumentVersion.document_id == document_id)
        .order_by(IngestionJob.created_at.desc())
    )
    return [IngestionJobOut.model_validate(j) for j in result.scalars().all()]


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    doc = await db.get(Document, document_id)
    if not doc or doc.is_deleted:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.is_deleted = True
    await db.flush()

    from app.services.retrieval_service import delete_document_vectors
    try:
        await delete_document_vectors(str(document_id))
    except Exception as e:
        logger.warning(f"Qdrant vector deletion failed for doc {document_id}: {e}")
