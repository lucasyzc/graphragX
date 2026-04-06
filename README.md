# GraphRAGX MVP

Powered by Graph AI. RAG for Everything.

GraphRAGX is an AI-powered platform foundation for repository indexing, graph + vector retrieval, and grounded answers for DevOps and development workflows.

## Current delivery (Step 1)

- FastAPI service with core MVP endpoints.
- Web console for project creation, sync trigger, and query execution.
- Project-level RBAC scaffolding via headers.
- Repository sync jobs with true incremental mode (`git diff` by base/head/since sha) + full fallback.
- Canonical IR model and language extractor interfaces (C#/Python/Java placeholders).
- Embedding gateway (`local_hash` + `openai_compatible`) and Qdrant real write/query.
- Chat gateway (`none` + `openai_compatible`) for grounded answer generation.
- Neo4j graph real write with symbol relations (`CONTAINS`/`CALLS`/`IMPORTS`/`REFERENCES`).
- Knowledge source ingest pipeline (local dir / HTTP) for doc chunks and unified query.
- Docker Compose stack for PostgreSQL, Neo4j, Qdrant, and API.

## Prerequisites (local components)

- PostgreSQL 14+ (required)
- Neo4j 5+ (required)
- Qdrant 1.7+ (required for vector retrieval flow)
- Git CLI (required for remote repository sync; local path mode does not need git clone/pull)

The API blocks startup if:
- `DATABASE_URL` points to SQLite in non-test environments.
- required services (PostgreSQL/Neo4j/Qdrant) are unreachable.

## Run locally

1. Create virtual environment and install:
   - `python -m venv .venv`
   - `.\.venv\Scripts\Activate.ps1`
   - `pip install -e .[dev]`
2. Copy env file:
   - `Copy-Item .env.example .env`
3. Update `.env` with your local credentials and endpoints.
4. Start API:
   - `uvicorn app.main:app --reload`

## Database migrations (Alembic)

- Install dev dependencies: `pip install -e .[dev]`
- Upgrade to latest revision: `alembic upgrade head`

Example:

```env
DATABASE_URL=postgresql+psycopg://postgres:your_password@localhost:5432/codegraphrag
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
QDRANT_URL=http://localhost:6333
QDRANT_UPSERT_BATCH_SIZE=256
QDRANT_UPSERT_MAX_RETRIES=2
REQUIRE_NEO4J=true
REQUIRE_QDRANT=true
ENABLE_EXTERNAL_STORES=true
EMBEDDING_PROVIDER=local_hash
# or openai_compatible:
# EMBEDDING_PROVIDER=openai_compatible
# EMBEDDING_API_BASE=http://your-openai-compatible-endpoint/v1
# EMBEDDING_API_KEY=your_key
# EMBEDDING_MODEL=text-embedding-3-small
# CHAT_PROVIDER=openai_compatible
# CHAT_API_BASE=http://your-openai-compatible-endpoint/v1
# CHAT_API_KEY=your_key
# CHAT_MODEL=gpt-4o-mini
# Alternative unified OpenAI-style vars (auto enables chat):
# OPENAI_API_KEY=your_key
# OPENAI_MODEL=gpt-4o-mini
# OPENAI_BASE_URL=http://your-openai-compatible-endpoint/v1
# OPENAI_WIRE_API=responses  # or chat_completions
```

After startup, verify chat wiring:

- `GET /health/chat-config` should return `enabled=true` and `configured=true`.
- In Search Center (`/`), "LLM 状态" and "回答模式" will show whether answer came from `model` or `fallback`.

## Run with containers

- `docker compose up --build`

## API surface

- `GET /projects`
- `POST /projects`
- `PATCH /projects/{project_id}`
- `GET /projects/{project_id}/memories`
- `POST /projects/{project_id}/memories`
- `PATCH /projects/{project_id}/memories/{memory_id}`
- `GET /projects/{project_id}/sync-status`
- `POST /projects/{project_id}/sync`
- `GET /jobs`
- `GET /jobs/{job_id}`
- `POST /query`
- `GET /chat/sessions`
- `POST /chat/sessions`
- `PATCH /chat/sessions/{session_id}`
- `GET /chat/sessions/{session_id}/messages`
- `POST /chat/sessions/{session_id}/messages`
- `GET /knowledge/sources`
- `POST /knowledge/sources`
- `PATCH /knowledge/sources/{source_id}`
- `POST /knowledge/sources/{source_id}/sync`
- `GET /knowledge/jobs`
- `POST /analysis/impact`
- `GET /symbols/{symbol_id}`
- `GET /health`
- `GET /health/dependencies`
- `GET /` (Chat Home)
- `GET /search` (Search Center)
- `GET /scenarios/impact` (Scenario page)
- `GET /manage/projects` (Project Management)
- `GET /manage/sync` (Sync Management)
- `GET /manage/knowledge` (Knowledge Management)
- `POST /query` now includes optional `contexts` and `retrieval_meta` fields in response.
- `POST /chat/sessions/{session_id}/messages` keeps `project_id_override` for compatibility but ignores it (deprecated).

## Header-based actor context (MVP)

- `X-User`: user identity (default `system`)
- `X-Role`: `viewer|editor|admin` (default `admin`)

Note: production auth should use SSO/JWT and server-side policy enforcement.

## Sync behavior in this step

- For `github/gitlab`, service checks out the repository under `.workspace/repos/{project_id}`.
- For `local`, service indexes the configured local directory directly.
- Incremental sync supports optional `base_sha` / `head_sha` / `since_sha`; when unavailable, it falls back to full sync.
- Symbol extraction currently supports:
  - Python: `class`, `def`, `async def` via AST.
  - Java/C#: class/interface/enum(+struct for C#) and method regex extraction.
- Python graph edges include `CALLS` / `IMPORTS` / `REFERENCES` (best-effort static extraction).
- Symbol snippets are persisted to `code_chunks`, embedded, and upserted into Qdrant.
- Knowledge docs (`md/txt/html/pdf/docx/json/jsonl`) are split into `document_chunks`, ACL-scoped, and available for keyword/vector retrieval.
- Symbols are synced to Neo4j and used by `/analysis/impact` for graph-hop expansion.
- Sync submission returns `409` if the same project already has a `queued/running` job.
- Stale active jobs are auto-marked `failed` after `SYNC_STALE_MINUTES`.
- Environment toggle:
  - `SYNC_MOCK_MODE=true` for deterministic test or demo behavior.

## Quick flow

1. Start API: `uvicorn app.main:app --reload`
2. Open Chat Home: `http://localhost:8000/`
3. Open Search Center (legacy): `http://localhost:8000/search`
4. Create project in Management Center: `http://localhost:8000/manage/projects`
5. Trigger sync in `http://localhost:8000/manage/sync`
6. Run query in Search Center and jump to impact scenario if needed.
