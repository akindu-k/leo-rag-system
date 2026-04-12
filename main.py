import asyncio
import sys
import warnings
import logging
from contextlib import asynccontextmanager

# psycopg requires SelectorEventLoop on Windows — uvicorn calls asyncio.new_event_loop()
# internally which respects the policy, so set_event_loop() alone is not enough.
# WindowsSelectorEventLoopPolicy is deprecated in 3.14 (removed in 3.16); suppress the
# warning for now — the functionality is unchanged in 3.14.
if sys.platform == "win32":
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.database import engine, Base

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────
    logger.info("Starting Leo RAG System...")

    # Create DB tables (Alembic handles migrations in production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Initialise Qdrant collection
    try:
        from app.services.retrieval_service import init_qdrant_collection
        await init_qdrant_collection()
        logger.info("Qdrant collection ready.")
    except Exception as e:
        logger.warning(f"Qdrant init skipped: {e}")

    # Initialise MinIO bucket
    try:
        from app.services.storage_service import init_storage
        await init_storage()
        logger.info("MinIO bucket ready.")
    except Exception as e:
        logger.warning(f"MinIO init skipped: {e}")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("Shutting down...")
    await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    docs_url=f"{settings.API_PREFIX}/docs",
    redoc_url=f"{settings.API_PREFIX}/redoc",
    lifespan=lifespan,
)

# CORS — wide open in dev; tighten in production via CORS_ORIGINS env var
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routers ───────────────────────────────────────────────────────────────
from app.api.auth import router as auth_router
from app.api.documents import router as documents_router
from app.api.chat import router as chat_router
from app.api.sessions import router as sessions_router

app.include_router(auth_router, prefix=settings.API_PREFIX)
app.include_router(documents_router, prefix=settings.API_PREFIX)
app.include_router(chat_router, prefix=settings.API_PREFIX)
app.include_router(sessions_router, prefix=settings.API_PREFIX)

# ── Static files (frontend) ───────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")


@app.get("/", include_in_schema=False)
async def serve_index():
    return FileResponse("frontend/index.html")


@app.get("/admin", include_in_schema=False)
async def serve_admin():
    return FileResponse("frontend/admin.html")


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=settings.DEBUG)
