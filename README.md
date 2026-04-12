# Leo RAG System

A production-grade **Retrieval-Augmented Generation (RAG)** chatbot for the Leo Movement. Answers user queries strictly from uploaded documents with cited sources. Built to connect to a mobile app.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [How It Works](#how-it-works)
- [Quick Start (Local)](#quick-start-local)
- [Configuration Reference](#configuration-reference)
- [API Reference](#api-reference)
- [Document Ingestion Pipeline](#document-ingestion-pipeline)
- [Retrieval & Answer Flow](#retrieval--answer-flow)
- [Access Control](#access-control)
- [Frontend](#frontend)
- [Deployment (Production)](#deployment-production)
- [Development Notes](#development-notes)

---

## Architecture Overview

```
User / Mobile App
        │
        ▼
   Nginx (TLS)
        │
        ▼
  FastAPI App (port 8000)
   ├── Auth (JWT)
   ├── Document Upload API  ──► MinIO (file storage)
   ├── Ingestion Worker     ──► OpenAI Embeddings ──► Qdrant (vectors)
   ├── Chat API (SSE stream)
   │    ├── OpenAI Embeddings (query)
   │    ├── Qdrant (vector search)
   │    ├── Cross-Encoder Reranker
   │    └── OpenAI GPT (answer generation)
   └── Session / Citation API
        │
        ▼
  PostgreSQL (metadata, sessions, citations)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| **API framework** | FastAPI (async) |
| **Database** | PostgreSQL 16 + SQLAlchemy 2.0 async |
| **Vector store** | Qdrant |
| **Object storage** | MinIO (S3-compatible) |
| **Embeddings** | OpenAI `text-embedding-3-small` |
| **Answer LLM** | OpenAI `gpt-4o-mini` (swappable) |
| **Reranker** | `cross-encoder/ms-marco-MiniLM-L-6-v2` (local, CPU) |
| **PDF parsing** | PyMuPDF (fast lane) + Unstructured (fallback) |
| **Auth** | JWT (python-jose) + bcrypt |
| **Streaming** | Server-Sent Events (SSE) |
| **Reverse proxy** | Nginx |
| **Containerisation** | Docker + Docker Compose |

---

## Project Structure

```
leo-rag-system/
│
├── main.py                        # FastAPI app entry point, lifespan hooks
│
├── app/
│   ├── api/
│   │   ├── auth.py                # Register, login, /me, change-password
│   │   ├── documents.py           # Upload, list, delete documents
│   │   ├── chat.py                # Streaming chat endpoint (SSE)
│   │   └── sessions.py            # CRUD for chat sessions
│   │
│   ├── core/
│   │   ├── config.py              # All settings via Pydantic / .env
│   │   ├── database.py            # Async SQLAlchemy engine + session factory
│   │   └── security.py            # JWT creation/decode, bcrypt, auth dependencies
│   │
│   ├── models/                    # SQLAlchemy ORM models
│   │   ├── user.py                # User, Group, UserGroup
│   │   ├── document.py            # Document, DocumentVersion, DocumentChunk,
│   │   │                          #   DocumentAccessRule, IngestionJob
│   │   └── chat.py                # ChatSession, ChatMessage, AnswerCitation
│   │
│   ├── schemas/                   # Pydantic request/response shapes
│   │   ├── auth.py
│   │   ├── document.py
│   │   └── chat.py
│   │
│   ├── services/
│   │   ├── storage_service.py     # MinIO upload / download / delete
│   │   ├── parsing_service.py     # PyMuPDF (lane 1) + Unstructured (lane 2)
│   │   ├── chunking_service.py    # Structure-first chunking with token limits
│   │   ├── embedding_service.py   # OpenAI embeddings, batched
│   │   ├── ingestion_service.py   # Orchestrates the full ingestion pipeline
│   │   ├── retrieval_service.py   # Qdrant vector search with permission filter
│   │   ├── reranking_service.py   # Cross-encoder reranking (lazy-loaded)
│   │   ├── answer_service.py      # OpenAI streaming chat completion
│   │   ├── citation_service.py    # Build and persist citations
│   │   └── session_service.py     # Chat session and history management
│   │
│   └── utils/
│       ├── permissions.py         # Resolve which documents a user can access
│       └── file_utils.py          # Extension checks, MinIO key builder
│
├── migrations/                    # Alembic async migrations
│   ├── env.py
│   └── versions/
│
├── frontend/
│   ├── index.html                 # Chat UI
│   ├── admin.html                 # Document upload & management UI
│   └── static/
│       ├── app.js                 # Chat logic, SSE streaming, session management
│       └── style.css              # Dark theme UI
│
├── nginx/
│   └── nginx.conf                 # Reverse proxy + SSL + SSE passthrough
│
├── docker-compose.yml             # Local development (PostgreSQL, Qdrant, MinIO)
├── docker-compose.prod.yml        # Production (all services + app + nginx)
├── Dockerfile                     # App container image
├── requirements.txt               # Python dependencies
├── .env.example                   # Local config template
├── .env.prod.example              # Production config template
└── alembic.ini                    # Alembic config
```

---

## How It Works

### Document Ingestion

When a document is uploaded:

1. File is validated (type + size) and saved to **MinIO**
2. A `Document`, `DocumentVersion`, and `IngestionJob` record are created in **PostgreSQL**
3. A **background task** starts the ingestion pipeline:
   - **Parse**: PyMuPDF extracts text page by page. If output is empty (scanned PDF, complex layout), Unstructured is used as fallback.
   - **Chunk**: Text is split by headings/structure first, then token-size limits are enforced (default 512 tokens, 64 overlap).
   - **Embed**: OpenAI `text-embedding-3-small` generates a vector per chunk (batched, 100 at a time).
   - **Index**: Vectors and full chunk payloads are upserted into **Qdrant**. Chunk metadata is also saved to PostgreSQL.
4. Job and version status are updated to `indexed`.

### Chat / Answer Flow

When a user sends a message:

1. **Auth**: JWT validated, session ownership checked.
2. **Permissions**: Documents accessible to this user are resolved from `document_access_rules`.
3. **Embed query**: The user's question is embedded with OpenAI.
4. **Vector search**: Qdrant returns the top-K most similar chunks filtered by accessible document IDs.
5. **Rerank**: A local cross-encoder scores each (query, chunk) pair and the top-N are kept.
6. **Generate**: The top-N chunks are injected into the system prompt and OpenAI streams the answer token by token via SSE.
7. **Persist**: The assistant message and citations are saved to PostgreSQL.
8. **Stream done**: A final SSE event carries the `message_id` and structured citations back to the client.

### Prompting Policy

The system prompt is strict and non-negotiable:

> Answer **only** from the provided document context. If the answer is not in the documents, say so explicitly. Cite every important claim with `[Document Title, p.PAGE]`.

---

## Quick Start (Local)

### Prerequisites
- Docker Desktop
- Python 3.11+ (tested on 3.14)
- OpenAI API key

### 1. Clone and configure

```bash
git clone <repo-url>
cd leo-rag-system
cp .env.example .env
```

Edit `.env` — the only required values are:

```ini
SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
```

### 2. Start infrastructure services

```bash
docker-compose up -d
```

Starts PostgreSQL (5432), Qdrant (6333), and MinIO (9000 / console: 9001).

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

> First install downloads PyTorch (~115 MB) and the reranker model (~91 MB, on first chat request).

### 4. Run the app

```bash
python main.py
```

| URL | Purpose |
|---|---|
| http://localhost:8000 | Chat UI |
| http://localhost:8000/admin | Document upload & management |
| http://localhost:8000/api/v1/docs | Swagger API docs |

### 5. First steps

1. Open the chat UI and **register** — the first account is automatically promoted to **admin**.
2. Go to **Admin** and upload a PDF, DOCX, or TXT file.
3. Watch the server logs — ingestion runs in the background and prints progress.
4. Once status shows `indexed`, ask a question in the chat.

---

## Configuration Reference

All settings are read from `.env` via Pydantic Settings. The app never has hard-coded secrets.

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(required)* | JWT signing secret — generate with `secrets.token_hex(32)` |
| `DATABASE_URL` | `postgresql+psycopg://leo:leo_password@localhost:5432/leo_rag` | Async PostgreSQL DSN |
| `STORAGE_ENDPOINT` | `localhost:9000` | MinIO / S3 endpoint |
| `STORAGE_ACCESS_KEY` | `minioadmin` | MinIO access key |
| `STORAGE_SECRET_KEY` | `minioadmin123` | MinIO secret key |
| `STORAGE_BUCKET` | `leo-documents` | Bucket name (auto-created) |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant base URL |
| `QDRANT_COLLECTION` | `leo_chunks` | Collection name (auto-created) |
| `QDRANT_VECTOR_SIZE` | `1536` | Must match embedding model output |
| `OPENAI_API_KEY` | *(required)* | Used for both embeddings and chat |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embedding model |
| `LLM_MODEL` | `gpt-4o-mini` | OpenAI chat model |
| `LLM_MAX_TOKENS` | `2048` | Max tokens in LLM response |
| `CHUNK_SIZE` | `512` | Max tokens per chunk |
| `CHUNK_OVERLAP` | `64` | Overlap between consecutive chunks |
| `RETRIEVAL_TOP_K` | `20` | Candidates fetched from Qdrant |
| `RERANKER_TOP_N` | `5` | Chunks passed to LLM after reranking |
| `RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | HuggingFace cross-encoder |
| `ALLOWED_EXTENSIONS` | `["pdf","docx","txt"]` | Accepted upload file types |
| `MAX_UPLOAD_SIZE_MB` | `50` | Upload size limit |
| `CORS_ORIGINS` | `["http://localhost:8000"]` | Allowed CORS origins (add mobile app origin here) |

---

## API Reference

Full interactive docs at `/api/v1/docs` when the server is running.

### Auth

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/auth/register` | Create account (first account = admin) |
| `POST` | `/api/v1/auth/login` | Returns JWT access token |
| `GET` | `/api/v1/auth/me` | Current user info |
| `POST` | `/api/v1/auth/change-password` | Change password |

### Documents

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/documents` | Upload a document (multipart/form-data) |
| `GET` | `/api/v1/documents` | List all documents with ingestion status |
| `GET` | `/api/v1/documents/{id}/jobs` | Ingestion job history for a document |
| `DELETE` | `/api/v1/documents/{id}` | Soft-delete + remove Qdrant vectors (admin only) |

### Chat Sessions

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/sessions` | Create a new chat session |
| `GET` | `/api/v1/sessions` | List user's sessions |
| `GET` | `/api/v1/sessions/{id}` | Get session with full message + citation history |
| `DELETE` | `/api/v1/sessions/{id}` | Delete a session |

### Chat

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/chat/{session_id}/messages` | Send message — returns SSE stream |

**SSE event format:**
```
data: {"type": "token", "content": "Hello"}
data: {"type": "token", "content": " world"}
data: {"type": "done", "message_id": "uuid", "citations": [...]}
data: {"type": "error", "message": "..."}
```

**Mobile app integration:** Use the same REST + SSE endpoints. Add the JWT token as `Authorization: Bearer <token>` on every request. For SSE, use a Fetch-based stream reader (EventSource doesn't support POST or custom headers).

---

## Document Ingestion Pipeline

```
Upload
  └─► MinIO (original file stored)
  └─► PostgreSQL (Document + DocumentVersion + IngestionJob records)
  └─► Background task:
        ├── Parse
        │     ├── Lane 1: PyMuPDF (fast, digital PDFs + TXT)
        │     └── Lane 2: Unstructured (scanned PDFs, DOCX, complex layouts)
        ├── Chunk
        │     ├── Split by headings / document structure
        │     ├── Enforce token-size limit (CHUNK_SIZE)
        │     └── Add overlap (CHUNK_OVERLAP)
        ├── Embed  (OpenAI, batches of 100)
        ├── Index  (Qdrant upsert — vector + full payload)
        └── Save chunk metadata → PostgreSQL
```

Each chunk stored in Qdrant carries this payload:

```json
{
  "document_id": "uuid",
  "document_version_id": "uuid",
  "document_title": "...",
  "file_name": "...",
  "page_number": 3,
  "section_title": "Eligibility Criteria",
  "chunk_index": 12,
  "content": "Full chunk text...",
  "language": "en"
}
```

---

## Retrieval & Answer Flow

```
User question
    │
    ▼
OpenAI embed query  →  1536-dim vector
    │
    ▼
Qdrant query_points
    ├── Filter: document_id IN [accessible doc IDs]
    └── Returns top-K (default 20) by cosine similarity
    │
    ▼
Cross-encoder rerank  →  top-N (default 5) chunks
    │
    ▼
Build prompt
    ├── System: strict grounding policy
    ├── Recent chat history (last 6 turns)
    └── User question + injected document context
    │
    ▼
OpenAI gpt-4o-mini  →  streamed tokens via SSE
    │
    ▼
Save assistant message + citations → PostgreSQL
```

---

## Access Control

Documents have access rules stored in `document_access_rules`:

| `subject_type` | `subject_id` | Meaning |
|---|---|---|
| `all` | `NULL` | Every authenticated user can read this document |
| `user` | `<user_id>` | Only this specific user |
| `group` | `<group_id>` | All members of this group |

By default, every uploaded document gets an `all` rule — all logged-in users can query it. Admins can restrict documents to specific users or groups by adding targeted rules.

**Permission filtering happens at the Qdrant query level** — the vector search only scans chunks belonging to documents the current user is allowed to access. It is impossible to leak content from restricted documents through the LLM.

---

## Frontend

Two pages served as static files from FastAPI:

### Chat UI (`/`)
- Session list sidebar with new chat / delete
- Streaming answer display (SSE)
- Collapsible citation cards under each answer (document title, page, excerpt)
- Auto-titles sessions from the first message
- Auth modal (register / login) — no separate auth page

### Admin UI (`/admin`)
- Drag-and-drop document upload with title/description fields
- Live document table with ingestion status badge (`pending`, `processing`, `indexed`, `failed`)
- Auto-refreshes status every 10 seconds
- Delete document (admin only)

**Mobile app:** The frontend is intentionally thin vanilla JS — no framework. The mobile app should integrate directly with the REST + SSE API using its own native UI. Add the mobile app's origin to `CORS_ORIGINS` in `.env`.

---

## Deployment (Production)

### Requirements
- Linux VPS — minimum **2 vCPU / 4 GB RAM** (reranker uses ~1 GB)
- Domain name pointed at the server
- Docker installed

### Steps

```bash
# 1. Get code onto server
git clone <repo> /opt/leo-rag && cd /opt/leo-rag

# 2. Configure
cp .env.prod.example .env
nano .env        # fill in all secrets
chmod 600 .env

# 3. Get SSL certificate
apt install certbot
certbot certonly --standalone -d your-domain.com
mkdir -p nginx/certs
cp /etc/letsencrypt/live/your-domain.com/{fullchain,privkey}.pem nginx/certs/

# 4. Update nginx.conf with your domain name
sed -i 's/your-domain.com/youractualdomain.com/g' nginx/nginx.conf

# 5. Deploy
docker-compose -f docker-compose.prod.yml up -d --build

# 6. View logs
docker-compose -f docker-compose.prod.yml logs -f app
```

### Useful operations

```bash
# Redeploy after code change
docker-compose -f docker-compose.prod.yml up -d --build app

# Backup database
docker exec leo_postgres pg_dump -U leo leo_rag > backup_$(date +%Y%m%d).sql

# SSL auto-renew (add to crontab)
0 3 * * * certbot renew --quiet && \
  cp /etc/letsencrypt/live/your-domain.com/*.pem /opt/leo-rag/nginx/certs/ && \
  docker-compose -f /opt/leo-rag/docker-compose.prod.yml restart nginx
```

### Managed service alternatives

| Service | Swap with |
|---|---|
| Self-hosted PostgreSQL | Supabase / AWS RDS — update `DATABASE_URL` |
| MinIO | AWS S3 — set `STORAGE_ENDPOINT=s3.amazonaws.com`, `STORAGE_USE_SSL=true` |
| Self-hosted Qdrant | Qdrant Cloud — update `QDRANT_URL` to their endpoint |

---

## Development Notes

### Windows + Python 3.14
`psycopg` (async PostgreSQL driver) requires `SelectorEventLoop` on Windows. The app sets this automatically at startup via `WindowsSelectorEventLoopPolicy`. This policy is deprecated in Python 3.14 and will be removed in Python 3.16 — a future fix will use uvicorn's `loop_factory`.

### Reranker model
The cross-encoder model (~91 MB) is downloaded from HuggingFace on the **first chat request** and cached locally. Subsequent starts load it from cache instantly. To pre-download it:

```bash
python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
```

### Switching LLM
Change `LLM_MODEL` in `.env`:
- `gpt-4o-mini` — cheap, fast (default)
- `gpt-4o` — higher quality
- Restore Anthropic: edit `answer_service.py` to use the `AsyncAnthropic` client and set `ANTHROPIC_API_KEY`

### Database migrations
The app auto-creates tables on startup (via `Base.metadata.create_all`). For schema changes in production, use Alembic:

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```
