"""Chat session and history management."""
import uuid
import logging
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.chat import ChatSession, ChatMessage
from app.models.user import User

logger = logging.getLogger(__name__)


async def create_session(db: AsyncSession, user_id: uuid.UUID, title: str = "New Chat") -> ChatSession:
    session = ChatSession(id=uuid.uuid4(), user_id=user_id, title=title)
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


async def get_user_sessions(db: AsyncSession, user_id: uuid.UUID) -> List[ChatSession]:
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc())
    )
    return result.scalars().all()


async def get_session(db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID) -> Optional[ChatSession]:
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.id == session_id, ChatSession.user_id == user_id)
        .options(selectinload(ChatSession.messages).selectinload(ChatMessage.citations))
    )
    return result.scalar_one_or_none()


async def delete_session(db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    session = await db.get(ChatSession, session_id)
    if not session or session.user_id != user_id:
        return False
    await db.delete(session)
    await db.flush()
    return True


async def add_message(
    db: AsyncSession,
    session_id: uuid.UUID,
    role: str,
    content: str,
) -> ChatMessage:
    msg = ChatMessage(id=uuid.uuid4(), session_id=session_id, role=role, content=content)
    db.add(msg)
    await db.flush()
    await db.refresh(msg)
    return msg


async def get_session_history(db: AsyncSession, session_id: uuid.UUID) -> List[dict]:
    """Return last N messages as plain dicts for the LLM context."""
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    messages = result.scalars().all()
    return [{"role": m.role, "content": m.content} for m in messages[-20:]]


async def update_session_title(db: AsyncSession, session_id: uuid.UUID, title: str):
    session = await db.get(ChatSession, session_id)
    if session:
        session.title = title[:200]
        await db.flush()
