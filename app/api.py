from __future__ import annotations

from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api_support.state import bind_app_state
from app.auth.repository import init_tables
from app.auth.router import router as auth_router
from app.config import settings as app_settings
from app.observability import setup_observability
from app.rag_milvus import build_rag_components
from app.routes.admin import router as admin_router
from app.routes.chat import router as chat_router
from app.routes.debug import router as debug_router
from app.routes.documents import router as documents_router
from app.routes.health import router as health_router
from app.routes.legacy_query import router as legacy_query_router
from app.routes.sessions import router as sessions_router
from app.services.memory_service import MemoryService
from app.tools import build_default_tool_registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=" * 60)
    print("FastAPI service starting; initializing RAG components...")
    observability_status = setup_observability()
    app.state.observability = observability_status
    if observability_status.enabled:
        print(
            "Phoenix tracing enabled: "
            f"project={observability_status.project_name}, endpoint={observability_status.endpoint}"
        )
    elif observability_status.error:
        print(f"Observability disabled: {observability_status.error}")
    else:
        print("Observability disabled: OBSERVABILITY_ENABLED=false")

    index, retriever, reranker, query_engine = build_rag_components()
    app.state.index = index
    app.state.retriever = retriever
    app.state.reranker = reranker
    app.state.query_engine = query_engine
    app.state.tool_registry = build_default_tool_registry()
    print("RAG components initialized: index / retriever / reranker / query_engine")

    print("Initializing auth database tables...")
    init_tables()
    print("Auth database tables initialized")

    redis_client = redis.Redis(
        host=app_settings.redis_host,
        port=app_settings.redis_port,
        db=app_settings.redis_db,
        password=app_settings.redis_password or None,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        max_connections=app_settings.redis_max_connections,
    )
    try:
        redis_client.ping()
        app.state.redis = redis_client
        app.state.memory_service = MemoryService(redis_client)
        print("Redis connected: session memory will be persisted")
    except Exception as exc:
        app.state.redis = None
        app.state.memory_service = MemoryService()
        print(f"Redis connection failed; falling back to in-memory sessions ({exc})")

    print("=" * 60)
    yield

    redis_state = getattr(app.state, "redis", None)
    if redis_state is not None:
        try:
            redis_state.close()
        except Exception:
            pass


app = FastAPI(lifespan=lifespan)
bind_app_state(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(health_router)
app.include_router(admin_router)
app.include_router(legacy_query_router)
app.include_router(documents_router)
app.include_router(sessions_router)
app.include_router(debug_router)
app.include_router(chat_router)

app.mount("/frontend", StaticFiles(directory="frontend/dist", html=True), name="frontend")


@app.get("/")
async def root():
    return RedirectResponse(url="/frontend/")
