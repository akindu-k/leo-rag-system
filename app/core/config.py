from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=True)

    # ── App ───────────────────────────────────────────────────────────────
    APP_NAME: str = "Leo RAG System"
    DEBUG: bool = False
    API_PREFIX: str = "/api/v1"

    # ── Security ──────────────────────────────────────────────────────────
    SECRET_KEY: str = "change-this-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 h

    # ── Database ──────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+psycopg://leo:leo_password@localhost:5432/leo_rag"

    # ── MinIO / S3 ────────────────────────────────────────────────────────
    STORAGE_ENDPOINT: str = "localhost:9000"
    STORAGE_ACCESS_KEY: str = "minioadmin"
    STORAGE_SECRET_KEY: str = "minioadmin123"
    STORAGE_BUCKET: str = "leo-documents"
    STORAGE_USE_SSL: bool = False

    # ── Qdrant ────────────────────────────────────────────────────────────
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""           # required for Qdrant Cloud
    QDRANT_COLLECTION: str = "leo_chunks"
    QDRANT_VECTOR_SIZE: int = 1536  # text-embedding-3-small

    # ── OpenAI ────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    EMBEDDING_MODEL: str = "text-embedding-3-small"

    # ── LLM (OpenAI chat) ─────────────────────────────────────────────────
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_MAX_TOKENS: int = 2048

    # ── Anthropic (optional, not used by default) ──────────────────────────
    ANTHROPIC_API_KEY: str = ""

    # ── RAG ───────────────────────────────────────────────────────────────
    CHUNK_SIZE: int = 512          # tokens per chunk
    CHUNK_OVERLAP: int = 64        # tokens of overlap
    RETRIEVAL_TOP_K: int = 20      # candidates from vector search
    RERANKER_TOP_N: int = 5        # chunks passed to LLM after reranking

    # ── Upload ────────────────────────────────────────────────────────────
    ALLOWED_EXTENSIONS: List[str] = ["pdf", "docx", "txt"]
    MAX_UPLOAD_SIZE_MB: int = 50

    # ── Reranker ──────────────────────────────────────────────────────────
    RERANKER_ENABLED: bool = True      # set False on low-memory hosts (< 1 GB RAM)
    RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # ── CORS ──────────────────────────────────────────────────────────────
    CORS_ORIGINS: List[str] = ["http://localhost:8000", "http://localhost:3000"]


settings = Settings()
