from typing import Optional, List
import uuid
from datetime import datetime
from pydantic import BaseModel


class ChatSessionCreate(BaseModel):
    title: Optional[str] = "New Chat"


class ChatSessionOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChatSessionListResponse(BaseModel):
    items: List[ChatSessionOut]


class CitationOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document_title: Optional[str]
    file_name: Optional[str]
    page_number: Optional[int]
    section_title: Optional[str]
    excerpt: Optional[str]
    relevance_score: Optional[float]

    model_config = {"from_attributes": True}


class ChatMessageOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    created_at: datetime
    citations: List[CitationOut] = []

    model_config = {"from_attributes": True}


class ChatMessageCreate(BaseModel):
    content: str


class HistoryMessage(BaseModel):
    role: str    # "user" or "assistant"
    content: str


class PublicChatRequest(BaseModel):
    content: str
    history: List[HistoryMessage] = []


class ChatHistoryResponse(BaseModel):
    session: ChatSessionOut
    messages: List[ChatMessageOut]
