# Leo RAG System

A production-grade **Retrieval-Augmented Generation (RAG) chatbot** for the Leo Movement. Users ask questions in natural language and receive answers sourced strictly from uploaded documents — fully cited, grounding-audited, and streamed in real time.

Designed to run as a hosted web app and connect to a mobile app via the same REST + SSE API.

---

## Table of Contents

- [What It Does](#what-it-does)
- [Architecture Overview](#architecture-overview)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [How It Works](#how-it-works)
- [Running Locally](#running-locally)
- [Deploying to Production (Free Stack)](#deploying-to-production-free-stack)
- [Environment Variable Reference](#environment-variable-reference)
- [API Reference](#api-reference)
- [Access Control](#access-control)
- [Mobile App Integration](#mobile-app-integration)
- [Development Notes](#development-notes)

---

## What It Does

- **Document ingestion** — admins upload PDF, DOCX, or TXT files. The system parses, chunks, embeds, and indexes them automatically in the background.
- **Grounded answers** — users ask questions in a chat UI. The system retrieves relevant chunks from the indexed documents and generates an answer using only that content. It will never fabricate or use outside knowledge.
- **Citations** — every answer includes structured references (document title, page number, section).
- **Grounding audit** — after each answer is generated, a second LLM call checks whether every claim is supported by the source chunks.
- **Streaming** — answers are streamed token-by-token so the UI feels responsive.
- **Permission control** — documents can be restricted to specific users or groups.

---

## Architecture Overview

```
Browser / Mobile App
        │
        ▼
   FastAPI App  (Render)
    │
    ├── Supabase PostgreSQL   ← user accounts, sessions, citations, document metadata
    ├── Qdrant Cloud          ← chunk vectors + full-text search index
    ├── Backblaze B2          ← original uploaded files (S3-compatible)
    └── OpenAI API            ← embeddings, LLM (decomposition, HyDE, answer, audit)
```

### Chat pipeline (per message)

```
User question
    │
    ├─ 1. Query Decomposition   LLM splits complex questions into sub-queries
    ├─ 2. HyDE Embedding        LLM writes a hypothetical answer → embed that
    ├─ 3. Hybrid Retrieval      Dense vector search + keyword search → RRF merge
    ├─ 4. Cross-Encoder Rerank  Local model scores each (query, chunk) pair
    ├─ 5. Answer Generation     GPT streams answer from top-N chunks
    └─ 6. Grounding Validation  LLM audits every claim against source chunks
```

---

## Tech Stack

| Layer | Technology | Why |
| --- | --- | --- |
| API framework | **FastAPI** (async) | Native async, auto-docs, SSE streaming |
| Database | **PostgreSQL** via Supabase | Relational integrity for users, docs, sessions |
| Vector store | **Qdrant Cloud** | Dense + full-text index, payload filtering |
| File storage | **Backblaze B2** | S3-compatible, 10 GB free, no card required |
| Embeddings | **OpenAI** `text-embedding-3-small` | 1536-dim, strong quality, very low cost |
| LLM | **OpenAI** `gpt-4o-mini` | Used for all 4 LLM call sites per query |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Local CPU model, ~91 MB, high precision |
| PDF parsing | **PyMuPDF** + **Unstructured** | Fast lane + OCR fallback for scanned PDFs |
| Auth | **JWT** + **bcrypt** | Stateless, secure, mobile-friendly |
| Streaming | **Server-Sent Events (SSE)** | Simple, HTTP-native, no WebSocket overhead |
| Hosting | **Render** | Docker deploy from GitHub, free tier available |

---

## Project Structure

```
leo-rag-system/
│
├── main.py                        # App entry point, startup hooks
├── Dockerfile                     # Container image
├── docker-compose.yml             # Local dev (PostgreSQL, Qdrant, MinIO)
├── requirements.txt               # Python dependencies
├── .env                           # Your secrets (never commit this)
├── .env.example                   # Template for local dev
│
├── app/
│   ├── api/
│   │   ├── auth.py                # Register, login, /me, change-password
│   │   ├── documents.py           # Upload, list, delete documents
│   │   ├── chat.py                # Streaming chat endpoint (SSE)
│   │   └── sessions.py            # Chat session CRUD
│   │
│   ├── core/
│   │   ├── config.py              # All settings loaded from .env
│   │   ├── database.py            # Async SQLAlchemy engine
│   │   └── security.py            # JWT, bcrypt, auth dependencies
│   │
│   ├── models/                    # SQLAlchemy ORM models
│   │   ├── user.py                # User, Group, UserGroup
│   │   ├── document.py            # Document, DocumentVersion, Chunk, AccessRule, Job
│   │   └── chat.py                # ChatSession, ChatMessage, AnswerCitation
│   │
│   ├── schemas/                   # Pydantic request/response types
│   │
│   ├── services/
│   │   ├── storage_service.py     # Backblaze B2 / S3 upload & download
│   │   ├── parsing_service.py     # PyMuPDF (fast) + Unstructured (fallback)
│   │   ├── chunking_service.py    # Structure-first chunking with token limits
│   │   ├── embedding_service.py   # OpenAI embeddings, batched
│   │   ├── ingestion_service.py   # Full ingestion pipeline orchestrator
│   │   ├── query_service.py       # HyDE + query decomposition
│   │   ├── retrieval_service.py   # Hybrid retrieval (dense + keyword) + RRF
│   │   ├── reranking_service.py   # Cross-encoder reranking (lazy-loaded)
│   │   ├── answer_service.py      # OpenAI streaming answer
│   │   ├── validation_service.py  # LLM-as-judge grounding audit
│   │   ├── citation_service.py    # Build + persist citations
│   │   └── session_service.py     # Session + message history
│   │
│   └── utils/
│       ├── permissions.py         # Resolve accessible document IDs per user
│       └── file_utils.py          # Extension + content-type helpers
│
├── frontend/
│   ├── index.html                 # Chat UI
│   ├── admin.html                 # Document management (admin only)
│   └── static/
│       ├── app.js                 # SSE streaming, session management
│       └── style.css              # Dark theme
│
└── nginx/
    └── nginx.conf                 # Reverse proxy + SSL + SSE passthrough
```

---

## How It Works

### Document Ingestion

1. Admin uploads a file via `/admin`
2. File is saved to **Backblaze B2**
3. Records are created in **PostgreSQL** (`Document`, `DocumentVersion`, `IngestionJob`)
4. A background task runs:
   - **Parse** — PyMuPDF for digital PDFs/TXT; Unstructured as fallback for scanned PDFs and DOCX
   - **Chunk** — split by headings first, then enforce 512-token limit with 64-token overlap
   - **Embed** — OpenAI `text-embedding-3-small`, batched 100 at a time
   - **Index** — vectors + full text upserted to Qdrant; chunk metadata saved to PostgreSQL
5. Status updates to `indexed`

### Chat Answer Flow

1. **Permissions** — resolve which document IDs the user can access
2. **Decompose** — LLM splits complex questions into ≤4 sub-queries
3. **HyDE** — LLM writes a hypothetical answer per sub-query; that text is embedded (not the raw question)
4. **Hybrid retrieval** — per sub-query: dense search (HyDE embedding) + keyword search (MatchText on Qdrant full-text index); all results merged with Reciprocal Rank Fusion
5. **Rerank** — local cross-encoder scores every (query, chunk) pair; top-5 kept
6. **Answer** — top-5 chunks injected into a strict system prompt; GPT streams tokens via SSE
7. **Grounding audit** — second LLM call checks every claim against the source chunks; verdict sent in the `done` SSE event

---

## Running Locally

### Requirements

- Docker Desktop
- Python 3.11+
- OpenAI API key

### Steps

```bash
# 1. Clone
git clone <repo-url>
cd leo-rag-system

# 2. Copy and fill in config
cp .env.example .env
# Edit .env — set SECRET_KEY and OPENAI_API_KEY at minimum

# 3. Start infrastructure (PostgreSQL, Qdrant, MinIO)
docker-compose up -d

# 4. Install dependencies
pip install -r requirements.txt

# 5. Run
python main.py
```

| URL | Purpose |
| --- | --- |
| `http://localhost:8000` | Chat UI |
| `http://localhost:8000/admin` | Document management |
| `http://localhost:8000/api/v1/docs` | Swagger API docs |

**First time:** Register an account — the first registered user is automatically made admin. Then go to `/admin` and upload a document.

---

## Deploying to Production (Free Stack)

This is the recommended zero-cost deployment using fully managed services. You only pay for OpenAI API usage.

### Services used

| Component | Service | Free limit |
| --- | --- | --- |
| App hosting | **Render** | 512 MB RAM (free) or 1 GB RAM ($7/mo) |
| PostgreSQL | **Supabase** | 500 MB, free forever |
| Vector store | **Qdrant Cloud** | 1 GB cluster, free forever |
| File storage | **Backblaze B2** | 10 GB, free, no card required |

> **RAM note:** The reranker model needs ~1 GB RAM. On the free 512 MB Render tier, set `RERANKER_ENABLED=false`. Upgrade to the $7/month Starter plan to enable it.

---

### Step 1 — Push code to GitHub

```bash
git remote add origin https://github.com/YOUR_USERNAME/leo-rag-system.git
git push -u origin main
```

Make sure `.env` is in `.gitignore` — it is by default.

---

### Step 2 — Supabase (PostgreSQL)

1. Sign up at [supabase.com](https://supabase.com) → New project
2. Go to **Project Settings → Database → Connection pooling**
3. Copy the **Session mode** connection string (port 5432)
4. Change the prefix from `postgresql://` to `postgresql+psycopg://`

Your `DATABASE_URL` will look like:

```text
postgresql+psycopg://postgres.xxxx:YOUR_PASSWORD@aws-1-REGION.pooler.supabase.com:5432/postgres
```

> **Important:** Use the **Session Pooler** URL (not the direct connection). Render is IPv4-only; the direct Supabase connection is IPv6 on the free tier and will fail to connect.

---

### Step 3 — Qdrant Cloud

1. Sign up at [cloud.qdrant.io](https://cloud.qdrant.io) → Create a **Free cluster**
2. Copy the **Cluster URL** (e.g. `https://xxxx.eu-west-1-0.aws.cloud.qdrant.io`)
3. Under **API Keys**, create a key and copy it

---

### Step 4 — Backblaze B2 (file storage)

1. Sign up at [backblaze.com](https://backblaze.com) → B2 Cloud Storage
2. **Buckets → Create Bucket** — name: `leo-documents`, Private
3. **App Keys → Add Application Key** — Read & Write access to `leo-documents`
4. Copy the **keyID** and **applicationKey** (shown only once)
5. Note the bucket **Endpoint** shown on the bucket page (e.g. `s3.us-east-005.backblazeb2.com`)

---

### Step 5 — Render (app hosting)

1. Sign up at [render.com](https://render.com) → New → Web Service
2. Connect your GitHub repo
3. Settings:
   - **Runtime:** Docker
   - **Branch:** `main`
   - **Instance Type:** Free (512 MB) or Starter ($7/mo for reranker)
4. Add all environment variables (see table below)
5. Deploy

Once deployed, your app is live at `https://your-app-name.onrender.com`.

---

### Full environment variable list for Render

Copy this, fill in your values, and paste into Render's Environment tab:

```ini
APP_NAME=Leo RAG System
DEBUG=false
API_PREFIX=/api/v1

# Generate: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=your_generated_secret_key
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# Supabase — use Session Pooler URL with postgresql+psycopg:// prefix
DATABASE_URL=postgresql+psycopg://postgres.xxxx:PASSWORD@aws-1-REGION.pooler.supabase.com:5432/postgres

# Qdrant Cloud
QDRANT_URL=https://xxxx.eu-west-1-0.aws.cloud.qdrant.io
QDRANT_API_KEY=your_qdrant_api_key
QDRANT_COLLECTION=leo_chunks
QDRANT_VECTOR_SIZE=1536

# Backblaze B2
STORAGE_ENDPOINT=s3.us-east-005.backblazeb2.com
STORAGE_ACCESS_KEY=your_backblaze_key_id
STORAGE_SECRET_KEY=your_backblaze_application_key
STORAGE_BUCKET=leo-documents
STORAGE_USE_SSL=true

# OpenAI
OPENAI_API_KEY=sk-...
EMBEDDING_MODEL=text-embedding-3-small
LLM_MODEL=gpt-4o-mini
LLM_MAX_TOKENS=2048

# RAG settings
CHUNK_SIZE=512
CHUNK_OVERLAP=64
RETRIEVAL_TOP_K=20
RERANKER_TOP_N=5
RERANKER_ENABLED=false        # set true only if instance has >= 1 GB RAM

# Upload
ALLOWED_EXTENSIONS=["pdf","docx","txt"]
MAX_UPLOAD_SIZE_MB=50

# CORS — set to your Render app URL (and mobile app origin if applicable)
CORS_ORIGINS=["https://your-app-name.onrender.com"]
```

---

### After deployment

1. Visit `https://your-app-name.onrender.com`
2. **Register** — the first account created is automatically admin
3. Go to `/admin` and upload a document
4. Wait for status to show `indexed` (check the table — it refreshes every 10 seconds)
5. Go back to the chat and ask a question

> **Free tier spin-down:** Render's free tier pauses after 15 minutes of inactivity and takes ~30 seconds to wake on the next request. To keep it always-on, use a free uptime monitor like [UptimeRobot](https://uptimerobot.com) to ping your URL every 10 minutes.

---

### Redeploying after a code change

```bash
git add .
git commit -m "your message"
git push
```

Render automatically redeploys on every push to `main`.

---

## Environment Variable Reference

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `SECRET_KEY` | Yes | — | JWT signing key — `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | Yes | — | Full async PostgreSQL DSN |
| `OPENAI_API_KEY` | Yes | — | Used for embeddings, LLM, HyDE, grounding audit |
| `QDRANT_URL` | Yes | `http://localhost:6333` | Qdrant base URL |
| `QDRANT_API_KEY` | Cloud only | `""` | Required for Qdrant Cloud |
| `QDRANT_COLLECTION` | No | `leo_chunks` | Collection name (auto-created) |
| `QDRANT_VECTOR_SIZE` | No | `1536` | Must match embedding model |
| `STORAGE_ENDPOINT` | Yes | `localhost:9000` | B2 / MinIO / S3 endpoint |
| `STORAGE_ACCESS_KEY` | Yes | — | Storage access key |
| `STORAGE_SECRET_KEY` | Yes | — | Storage secret key |
| `STORAGE_BUCKET` | No | `leo-documents` | Bucket name (auto-created) |
| `STORAGE_USE_SSL` | No | `false` | Set `true` for B2 / S3 |
| `EMBEDDING_MODEL` | No | `text-embedding-3-small` | OpenAI embedding model |
| `LLM_MODEL` | No | `gpt-4o-mini` | OpenAI chat model for all LLM calls |
| `LLM_MAX_TOKENS` | No | `2048` | Max answer length |
| `CHUNK_SIZE` | No | `512` | Max tokens per chunk |
| `CHUNK_OVERLAP` | No | `64` | Token overlap between chunks |
| `RETRIEVAL_TOP_K` | No | `20` | Candidates fetched before reranking |
| `RERANKER_TOP_N` | No | `5` | Chunks passed to LLM after reranking |
| `RERANKER_ENABLED` | No | `true` | Set `false` on hosts with < 1 GB RAM |
| `RERANKER_MODEL` | No | `cross-encoder/ms-marco-MiniLM-L-6-v2` | HuggingFace reranker |
| `ALLOWED_EXTENSIONS` | No | `["pdf","docx","txt"]` | Accepted file types |
| `MAX_UPLOAD_SIZE_MB` | No | `50` | Max upload size |
| `CORS_ORIGINS` | No | `["http://localhost:8000"]` | Allowed origins — add mobile app URL here |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `1440` | JWT lifetime (default: 24 hours) |

---

## API Reference

Full interactive docs at `/api/v1/docs`.

### Auth

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/auth/register` | Create account — first account is auto-promoted to admin |
| `POST` | `/api/v1/auth/login` | Returns a JWT access token |
| `GET` | `/api/v1/auth/me` | Returns current user info including role |
| `POST` | `/api/v1/auth/change-password` | Change password |

### Documents (admin only for upload/delete)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/documents` | Upload a document (multipart/form-data) |
| `GET` | `/api/v1/documents` | List all documents with ingestion status |
| `GET` | `/api/v1/documents/{id}/jobs` | Ingestion job history for a document |
| `DELETE` | `/api/v1/documents/{id}` | Soft-delete + remove vectors (admin only) |

### Chat Sessions

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/sessions` | Create a new chat session |
| `GET` | `/api/v1/sessions` | List your sessions |
| `GET` | `/api/v1/sessions/{id}` | Get session with full message + citation history |
| `DELETE` | `/api/v1/sessions/{id}` | Delete a session |

### Chat

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/chat/{session_id}/messages` | Send a message — returns SSE stream |

**SSE events:**

```text
data: {"type": "token",  "content": "The membership fee..."}
data: {"type": "done",   "message_id": "uuid", "citations": [...], "grounding": {"grounded": true, "confidence": 0.96, "issues": null}}
data: {"type": "error",  "message": "An internal error occurred."}
```

**Authentication:** Every request needs `Authorization: Bearer <token>` in the header.

---

## Access Control

Every document has access rules in `document_access_rules`:

| `subject_type` | Meaning |
|---|---|
| `all` | Every logged-in user can query this document |
| `user` | Only a specific user |
| `group` | All members of a specific group |

By default, every uploaded document is accessible to all logged-in users (`subject_type = all`). Permission filtering is applied **inside the Qdrant query** — restricted content never reaches the application layer.

Admins bypass all permission filters and can access everything.

---

## Mobile App Integration

The mobile app connects to the same API — no separate backend needed.

1. **Auth:** `POST /api/v1/auth/login` → store the JWT token
2. **All requests:** add `Authorization: Bearer <token>` header
3. **Chat:** `POST /api/v1/chat/{session_id}/messages` returns an SSE stream
   - Use a Fetch-based stream reader, not `EventSource` (EventSource doesn't support POST or custom headers)
   - Read `data:` lines and parse each as JSON
4. **CORS:** add your mobile app's origin to `CORS_ORIGINS` in the environment variables

---

## Development Notes

### Known issue: psycopg + Supabase connection pooler

Supabase uses PgBouncer as a connection pooler. By default, `psycopg3` uses server-side prepared statements which conflict with PgBouncer. The engine is configured with `prepare_threshold=None` to disable this:

```python
engine = create_async_engine(
    settings.DATABASE_URL,
    connect_args={"prepare_threshold": None},
)
```

If you switch to a different PostgreSQL host without a pooler, you can remove this.

### Windows + Python 3.14

`psycopg` requires `SelectorEventLoop` on Windows. `main.py` sets this automatically via `WindowsSelectorEventLoopPolicy`. This is handled only in local development — Linux (Docker/Render) is unaffected.

### Reranker model

Downloaded from HuggingFace on first chat request (~91 MB) and cached. Pre-download it with:

```bash
python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
```

Set `RERANKER_ENABLED=false` on hosts with less than 1 GB available RAM.

### OpenAI calls per message

Each chat message makes up to 4 LLM calls:

| Call | Purpose | ~Output tokens |
| --- | --- | --- |
| Decomposition | Split complex question | 50–150 |
| HyDE (×N sub-queries) | Write hypothetical answer | 100–200 each |
| Answer | Grounded streaming answer | Up to `LLM_MAX_TOKENS` |
| Grounding audit | Validate claims against sources | 50–100 |

For a simple question (N=1 sub-query), that's 4 calls total. `gpt-4o-mini` keeps this affordable.

### Switching LLM

Change `LLM_MODEL` in `.env` — it applies to all four call sites simultaneously:

- `gpt-4o-mini` — fast, cheap (default)
- `gpt-4o` — higher quality
