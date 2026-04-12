"""Chat session management endpoints."""
import uuid
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.chat import ChatSessionCreate, ChatSessionOut, ChatSessionListResponse, ChatHistoryResponse
from app.services import session_service

router = APIRouter(prefix="/sessions", tags=["sessions"])
logger = logging.getLogger(__name__)


@router.post("", response_model=ChatSessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: ChatSessionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await session_service.create_session(db, current_user.id, payload.title or "New Chat")
    return ChatSessionOut.model_validate(session)


@router.get("", response_model=ChatSessionListResponse)
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sessions = await session_service.get_user_sessions(db, current_user.id)
    return ChatSessionListResponse(items=[ChatSessionOut.model_validate(s) for s in sessions])


@router.get("/{session_id}", response_model=ChatHistoryResponse)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await session_service.get_session(db, session_id, current_user.id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    from app.schemas.chat import ChatMessageOut, CitationOut
    messages = [
        ChatMessageOut(
            id=m.id,
            session_id=m.session_id,
            role=m.role,
            content=m.content,
            created_at=m.created_at,
            citations=[CitationOut.model_validate(c) for c in m.citations],
        )
        for m in session.messages
    ]
    return ChatHistoryResponse(session=ChatSessionOut.model_validate(session), messages=messages)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    deleted = await session_service.delete_session(db, session_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
