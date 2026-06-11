# WeiQuiz

Enterprise Agentic RAG system for private knowledge-base Q&A.

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.x-42b883?logo=vue.js&logoColor=white)](https://vuejs.org/)
[![LlamaIndex](https://img.shields.io/badge/LlamaIndex-RAG-6f42c1)](https://www.llamaindex.ai/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

WeiQuiz is an end-to-end Agentic RAG application for enterprise knowledge-base scenarios. It covers document ingestion, hybrid retrieval, query planning, multi-turn memory, streaming answers, source citations, observability, authentication, and basic admin workflows.

The project is designed as a practical engineering reference rather than a minimal vector-search demo. It focuses on problems that appear in real RAG systems: low-quality parsing, incomplete chunks, weak recall, multi-hop questions, hallucination control, session memory, traceability, and user isolation.

## Features

- Agentic RAG workflow: intent routing, strategy selection, HyDE, step-back queries, sub-question decomposition, rewrite and retry.
- Hybrid retrieval: dense vector retrieval plus BM25, RRF fusion, DashScope rerank, retrieval cache, and configurable Top-K.
- Hierarchical chunks: root, parent, and leaf nodes. Retrieve small chunks, then expand to parent context or auto-merge sibling chunks.
- Document ingestion: PDF, DOCX, Markdown, HTML, and text parsing with SHA256 incremental indexing and ingestion reports.
- Table-aware context: table merge/split helpers and table context post-processing for financial or report-style documents.
- Memory system: Redis recent context, PostgreSQL full chat history, rolling session summaries, and optional Mem0 long-term memory.
- Streaming UX: Server-Sent Events for route, step, trace, token, result, and citation updates.
- Auth and admin: JWT login, user roles, session isolation, knowledge-base document management, and audit logs.
- Observability: structured workflow trace, source payloads, ingestion report, and optional Phoenix tracing.
- Local-first stack: FastAPI backend, Vue frontend, PostgreSQL, Redis, Chroma for local development, and optional Milvus.

## Architecture

```text
Vue frontend
  -> FastAPI API
  -> AgentController
  -> Query Router / Tool Planner / Memory Policy
  -> Agentic RAG Workflow
  -> Hybrid Retrieval: vector + BM25 + RRF + rerank
  -> Parent Context / Auto-merging / Table Context
  -> LLM answer generation and optional grounding
  -> SSE response with answer, citations, route, and trace

Storage:
  PostgreSQL: users, sessions, messages, summaries, parent chunks, audit data
  Redis: recent chat memory, ingestion job state, lightweight caches
  Chroma/Milvus/pgvector: vector index backend
```

For a deeper walkthrough, see [docs/architecture.md](docs/architecture.md).

## Tech Stack

| Layer | Technology |
| --- | --- |
| Backend | Python, FastAPI, Uvicorn, Pydantic Settings, SQLAlchemy |
| RAG | LlamaIndex, OpenAI-compatible LLM APIs, DashScope embeddings/rerank |
| Vector store | Chroma by default, Milvus or pgvector as optional backends |
| Data services | PostgreSQL, Redis |
| Frontend | Vue 3, TypeScript, Vite, Pinia, Tailwind CSS, lucide-vue-next |
| Evaluation | pytest, RAGAS, retrieval ablation scripts |
| Observability | Structured trace payloads, optional Arize Phoenix |

## Project Structure

```text
app/
  agentic/       Agent controller, router, query transforms, workflow, grounding
  auth/          JWT auth, registration, login, RBAC dependencies
  eval/          Retrieval and RAG evaluation scripts
  ingest/        Document parsing, incremental indexing, OCR helpers
  llm/           Centralized OpenAI-compatible LLM gateway
  retrieval/     BM25 state, cache, parent/table/auto-merging context
  services/      Session memory and long-term memory services
  storage/       SQLAlchemy models and parent chunk store
  tools/         Tool registry, planner, web search and MCP adapters
frontend/        Vue application
docs/            Architecture, troubleshooting, and implementation notes
docker/          Database initialization files
tests/           Unit and workflow tests
```

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Node.js 18+
- Docker and Docker Compose
- DashScope API key or another OpenAI-compatible LLM/embedding provider

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at least:

```env
LLM_API_KEY=your-api-key
QWEN_LLM_API_KEY=your-api-key
JWT_SECRET_KEY=replace-with-a-long-random-secret
```

By default, WeiQuiz uses Chroma for local vector storage. Milvus can be enabled when you need a production-like vector service.

### 3. Start infrastructure

```bash
docker compose up -d redis postgres
```

To also run Milvus:

```bash
docker compose --profile milvus up -d redis postgres etcd minio milvus
```

### 4. Install backend dependencies

```bash
uv sync
```

If you do not use `uv`, install the project with pip:

```bash
python -m pip install -e .
```

### 5. Start backend

```bash
uv run uvicorn app.api:app --host 0.0.0.0 --port 8000 --reload
```

API docs are available at:

```text
http://localhost:8000/docs
```

### 6. Start frontend

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://localhost:5173
```

## First Admin User

Set an invite code in `.env`:

```env
ADMIN_INVITE_CODE=replace-with-admin-invite-code
```

Then register through the frontend or `POST /auth/register` with the same `admin_invite_code`. Users registered without the invite code receive the `user` role.

## Ingest Documents

Documents can be uploaded from the frontend knowledge-base panel or through the API.

Supported file types:

- `.txt`
- `.md`
- `.markdown`
- `.pdf`
- `.docx`
- `.html`
- `.htm`

The ingestion pipeline scans changes with SHA256, parses documents into blocks, builds hierarchical chunks, writes retrieval nodes to the vector backend, persists parent context in PostgreSQL, updates BM25 state, and emits an ingestion report under `data/audit`.

## Configuration

Most settings are managed in [app/config.py](app/config.py) and can be overridden from `.env`.

Important groups:

- LLM and embedding: `LLM_API_KEY`, `QWEN_LLM_API_KEY`, `LLM_API_BASE`, `LLM_MODEL`, `EMBEDDING_MODEL`
- Retrieval: `TOP_K`, `HIERARCHICAL_CHUNK_SIZES`, `RERANK_ENABLED`, `AUTO_MERGING_ENABLED`
- Storage: `VECTOR_STORE_BACKEND`, `CHROMA_DIR`, `MILVUS_URI`, `POSTGRES_URL`, `REDIS_HOST`
- Auth: `JWT_SECRET_KEY`, `ADMIN_INVITE_CODE`
- Observability: `OBSERVABILITY_ENABLED`, `PHOENIX_ENDPOINT`
- Tools and memory: `WEB_SEARCH_ENABLED`, `MCP_SERVER_URL`, `MEM0_ENABLED`

## Testing

Run the unit and workflow tests:

```bash
uv run pytest
```

Run a focused workflow test:

```bash
uv run pytest tests/test_rag_workflow.py -v
```

Evaluation scripts live in `app/eval/` and `scripts/`.

## Observability

The `/chat/stream` endpoint emits structured SSE events for routing, memory loading, retrieval, workflow steps, generation chunks, final sources, and trace payloads.

Optional Phoenix tracing can be enabled:

```env
OBSERVABILITY_ENABLED=true
PHOENIX_ENDPOINT=http://localhost:6006/v1/traces
PHOENIX_PROJECT_NAME=weiquiz-agentic-rag
```

Start Phoenix separately:

```bash
uv run phoenix serve
```

## Roadmap

- Document-level ACL filtering in retrieval.
- Safer SQL tool with read-only connections, table allowlists, AST validation, forced limits, and audit logs.
- Stronger evaluation reports for dense-only, BM25-only, hybrid, rerank, auto-merging, HyDE, and step-back ablations.
- More robust OCR and table extraction for scanned enterprise reports.
- Better tool result synthesis instead of returning raw tool payloads.
- Deployment examples for cloud environments.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## Security

Do not commit `.env`, local document data, indexes, API keys, or generated evaluation datasets. The repository ignores these by default.

If you find a security issue, please open a private report or contact the maintainers before publishing details.

## License

WeiQuiz is released under the [MIT License](LICENSE).
