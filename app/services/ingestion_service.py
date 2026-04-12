"""Orchestrates the full document ingestion pipeline."""
import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.document import Document, DocumentVersion, DocumentChunk, IngestionJob
from app.services import parsing_service, chunking_service, embedding_service, storage_service
from app.services.retrieval_service import upsert_chunks_to_qdrant

logger = logging.getLogger(__name__)


async def run_ingestion(job_id: uuid.UUID) -> None:
    """
    Full ingestion pipeline — runs as a background task.
    Steps: download → parse → chunk → embed → index → update status
    """
    async with AsyncSessionLocal() as db:
        try:
            await _run(db, job_id)
        except Exception as e:
            logger.error(f"Ingestion job {job_id} failed: {e}", exc_info=True)
            await _mark_failed(db, job_id, str(e))


async def _run(db: AsyncSession, job_id: uuid.UUID) -> None:
    # ── Load job ──────────────────────────────────────────────────────────
    job_result = await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
    job = job_result.scalar_one()

    version_result = await db.execute(
        select(DocumentVersion).where(DocumentVersion.id == job.document_version_id)
    )
    version = version_result.scalar_one()

    doc_result = await db.execute(select(Document).where(Document.id == version.document_id))
    doc = doc_result.scalar_one()

    # ── Mark running ─────────────────────────────────────────────────────
    job.status = "running"
    job.started_at = datetime.now(timezone.utc)
    version.status = "processing"
    await db.commit()

    logger.info(f"[Job {job_id}] Starting ingestion for document '{doc.title}'")

    # ── Download file from storage ────────────────────────────────────────
    file_bytes = await storage_service.download_file(version.storage_path)

    # ── Parse document ────────────────────────────────────────────────────
    parsed = await parsing_service.parse_document(file_bytes, doc.file_type, doc.file_name)
    logger.info(f"[Job {job_id}] Parsed {len(parsed.pages)} pages via {parsed.parse_method}")

    # ── Chunk document ────────────────────────────────────────────────────
    chunks = chunking_service.chunk_document(parsed)
    logger.info(f"[Job {job_id}] Created {len(chunks)} chunks")

    if not chunks:
        raise RuntimeError("Document produced no chunks — check if the file has extractable text.")

    # ── Embed chunks ──────────────────────────────────────────────────────
    texts = [c.content for c in chunks]
    embeddings = await embedding_service.embed_texts(texts)
    logger.info(f"[Job {job_id}] Generated {len(embeddings)} embeddings")

    # ── Prepare Qdrant payloads ───────────────────────────────────────────
    qdrant_points = []
    db_chunks = []

    for chunk, embedding in zip(chunks, embeddings):
        point_id = uuid.uuid4()
        payload = {
            "document_id": str(doc.id),
            "document_version_id": str(version.id),
            "document_title": doc.title,
            "file_name": doc.file_name,
            "page_number": chunk.page_number,
            "section_title": chunk.section_title,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "language": parsed.language,
        }
        qdrant_points.append({"id": str(point_id), "vector": embedding, "payload": payload})

        db_chunks.append(
            DocumentChunk(
                id=uuid.uuid4(),
                document_id=doc.id,
                document_version_id=version.id,
                qdrant_point_id=point_id,
                chunk_index=chunk.chunk_index,
                page_number=chunk.page_number,
                section_title=chunk.section_title,
                content_preview=chunk.content[:280],
                token_count=chunk.token_count,
            )
        )

    # ── Upsert to Qdrant ──────────────────────────────────────────────────
    await upsert_chunks_to_qdrant(qdrant_points)
    logger.info(f"[Job {job_id}] Indexed {len(qdrant_points)} vectors in Qdrant")

    # ── Save chunk metadata to PostgreSQL ────────────────────────────────
    db.add_all(db_chunks)

    # ── Mark completed ────────────────────────────────────────────────────
    job.status = "completed"
    job.chunks_processed = len(chunks)
    job.completed_at = datetime.now(timezone.utc)
    version.status = "indexed"
    version.indexed_at = datetime.now(timezone.utc)

    await db.commit()
    logger.info(f"[Job {job_id}] Ingestion completed successfully.")


async def _mark_failed(db: AsyncSession, job_id: uuid.UUID, error_msg: str) -> None:
    try:
        job_result = await db.execute(select(IngestionJob).where(IngestionJob.id == job_id))
        job = job_result.scalar_one_or_none()
        if job:
            job.status = "failed"
            job.error_message = error_msg[:2000]
            job.completed_at = datetime.now(timezone.utc)

            version_result = await db.execute(
                select(DocumentVersion).where(DocumentVersion.id == job.document_version_id)
            )
            version = version_result.scalar_one_or_none()
            if version:
                version.status = "failed"
                version.error_message = error_msg[:2000]

            await db.commit()
    except Exception as e:
        logger.error(f"Failed to mark job {job_id} as failed: {e}")
