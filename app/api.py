import asyncio
import concurrent.futures
import json
import os
import re
import time
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Optional

import redis
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel
from llama_index.core.schema import QueryBundle
from starlette.concurrency import run_in_threadpool

from app.config import settings as app_settings
from app.rag_milvus import build_rag_components
from app.ingest.sync import diff_docs, load_state, save_state
from app.ingest.milvus_loader import apply_diff_to_milvus
from app.agentic.router import QueryStrategy, RouteResult
from app.agentic.controller import AgentController, AgentDecision, AgentMode
from app.agentic.llama_workflow import AgenticRAGWorkflow, WorkflowStepEvent
from app.agentic.node_synthesizer import (
    build_fallback_answer_from_intermediate,
    build_citations_from_nodes,
    format_memory_context,
    stream_answer_from_nodes,
    synthesize_intermediate_answers,
)
from app.agentic.grounding import check_answer_grounding, should_run_grounding
from app.agentic.rag_workflow import WorkflowTrace
from app.metadata_schema import SourceNodePayload
from app.retrieval.cache import RetrievalCache
from app.services.memory_service import MemoryService
from app.services.long_term_memory_service import LongTermMemoryService
from app.tools import ToolRegistry, build_default_tool_registry
from app.auth.router import router as auth_router
from app.auth.repository import (
    init_tables,
    get_db,
    get_chat_session,
    create_chat_session,
    list_user_sessions,
    list_chat_messages,
    soft_delete_session,
    update_session_last_message,
)
from app.auth.dependencies import get_current_user, require_admin
from app.storage.auth_models import User, ChatSession
from sqlalchemy.orm import Session


SUPPORTED_UPLOAD_EXTS = {".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_ingest_lock = threading.Lock()
INGEST_JOB_TTL = 60 * 60 * 24
WORKFLOW_RETRIEVAL_TIMEOUT_SECONDS = 20
LIGHTWEIGHT_CHAT_TIMEOUT_SECONDS = 30


def _ingest_job_key(job_id: str) -> str:
    return f"rag:ingest:job:{job_id}"


def _schedule_memory_compression(
    background_tasks: BackgroundTasks,
    memory_service: MemoryService,
    session_id: str,
    owner_user_id: str,
) -> None:
    """Compress old chat messages after the response path has persisted them."""

    background_tasks.add_task(
        memory_service.compress_session_in_background,
        session_id,
        owner_user_id,
    )


def _should_write_long_term_memory(user_message: str, assistant_answer: str) -> bool:
    user_text = (user_message or "").strip()
    assistant_text = (assistant_answer or "").strip()
    if not user_text or not assistant_text:
        return False
    if _is_low_value_long_term_memory_content(assistant_text):
        return False

    explicit_memory_markers = (
        "记住",
        "请记住",
        "帮我记住",
        "remember",
    )
    long_term_markers = (
        "我的目标",
        "我的偏好",
        "我希望",
        "我正在",
        "我当前",
        "以后",
        "下次",
        "项目是",
        "系统是",
        "架构是",
        "当前记忆系统",
    )
    return any(marker in user_text for marker in explicit_memory_markers + long_term_markers)


def _is_low_value_long_term_memory_content(content: str) -> bool:
    low_value_markers = (
        "[error]",
        "Answer generation failed",
        "Request timed out",
        "无法回答",
        "知识库内容不相关",
        "完全不相关",
        "缺少相关信息",
        "没有任何关于",
    )
    return any(marker in content for marker in low_value_markers)


def _schedule_long_term_memory_add(
    background_tasks: BackgroundTasks,
    long_term_memory_service: LongTermMemoryService,
    user_id: str,
    user_message: str,
    assistant_answer: str,
) -> None:
    if not _should_write_long_term_memory(user_message, assistant_answer):
        return
    if app_settings.mem0_async_add:
        background_tasks.add_task(
            long_term_memory_service.add,
            user_id,
            [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": assistant_answer},
            ],
        )
    else:
        long_term_memory_service.add(
            user_id,
            [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": assistant_answer},
            ],
        )


def _memory_compression_skip_reason(compressed: bool, plan: dict, after: dict) -> str:
    if compressed:
        return ""
    if plan["message_count"] <= plan["trigger_messages"]:
        return "message_count_not_exceed_trigger"
    if plan["evicted_message_count"] <= 0:
        return "no_evicted_messages"
    if plan["new_message_count"] <= 0:
        return "no_new_messages_after_summary_boundary"
    if not after["summary"]["exists"]:
        return "summary_generation_returned_empty"
    return "not_compressed"


def _stream_lightweight_chat(message: str, memory_context) -> Iterator[str]:
    """Generate a lightweight memory-aware chat answer without RAG retrieval."""

    prompt = (
        "请基于会话记忆回答用户问题。\n"
        "如果用户是在要求你记住某个事实，要简短确认；如果用户追问前文，要优先使用会话记忆。\n"
        "不要声称已经查询知识库，也不要编造会话记忆之外的事实。\n\n"
        f"{format_memory_context(memory_context)}"
        f"【用户输入】\n{message}"
    )
    client = OpenAI(
        api_key=app_settings.llm_api_key,
        base_url=app_settings.llm_api_base,
        timeout=LIGHTWEIGHT_CHAT_TIMEOUT_SECONDS,
    )
    response = client.chat.completions.create(
        model=app_settings.llm_model,
        messages=[
            {
                "role": "system",
                "content": "你是 WeiQuiz 的轻量会话助手，只处理普通对话和多轮记忆追问。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        stream=True,
    )
    for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        token = getattr(delta, "content", None) if delta is not None else None
        if token:
            yield token


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("=" * 60)
    print("🚀 FastAPI 服务启动中，初始化 RAG 组件...")
    index, retriever, reranker, query_engine = build_rag_components()
    app.state.index = index
    app.state.retriever = retriever
    app.state.reranker = reranker
    app.state.query_engine = query_engine
    app.state.tool_registry = build_default_tool_registry()
    print("✅ RAG 组件初始化完成：index / retriever / reranker / query_engine")

    # 初始化认证数据库表
    print("🔧 初始化认证数据库表...")
    init_tables()
    print("✅ 认证数据库表初始化完成")

    r = redis.Redis(
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
        r.ping()
        app.state.redis = r
        app.state.memory_service = MemoryService(r)
        print("✅ Redis 连接成功：会话记忆将持久化")
    except Exception as e:
        app.state.redis = None
        app.state.memory_service = MemoryService()
        print(f"⚠️ Redis 连接失败：将降级为内存会话（{e}）")

    print("=" * 60)
    yield
    r = getattr(app.state, "redis", None)
    if r is not None:
        try:
            r.close()
        except Exception:
            pass

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册认证路由
app.include_router(auth_router)

# 托管前端静态文件（Vite构建产物）
app.mount("/frontend", StaticFiles(directory="frontend/dist", html=True), name="frontend")


@app.get("/")
async def root():
    """重定向到登录页面"""
    return RedirectResponse(url="/frontend/")


class QueryRequest(BaseModel):
    question: str


class ChatRequest(BaseModel):
    session_id: str
    message: str
    grounding_mode: str = "off"


def _route_payload(route: RouteResult) -> dict:
    return {
        "intent": route.intent.value,
        "method": route.method,
        "reason": route.reason,
        "confidence": route.confidence,
        "query_strategy": route.query_strategy.value,
        "complexity": route.complexity,
        "tools": route.tools or [],
        "normalized_query": route.normalized_query,
        "need_grounding": route.need_grounding,
    }


def _controller_decision_payload(decision: AgentDecision) -> dict:
    tool_plan = decision.tool_plan
    return {
        "mode": decision.mode.value,
        "reason": decision.reason,
        "rag_strategy": decision.rag_strategy.value,
        "need_grounding": decision.need_grounding,
        "max_retries": decision.max_retries,
        "memory_policy": {
            "use_recent_messages": decision.memory_policy.use_recent_messages,
            "use_session_summary": decision.memory_policy.use_session_summary,
            "use_long_term_memory": decision.memory_policy.use_long_term_memory,
            "long_term_top_k": decision.memory_policy.long_term_top_k,
        },
        "clarification": {
            "needed": decision.clarification.needed,
            "question": decision.clarification.question,
            "reason": decision.clarification.reason,
            "missing_slots": list(decision.clarification.missing_slots),
            "method": decision.clarification.method,
        },
        "tool_plan": None
        if tool_plan is None
        else {
            "method": tool_plan.method,
            "tool_name": tool_plan.tool_name,
            "arguments": tool_plan.arguments,
            "error": tool_plan.error,
        },
    }


def _source_node_payload(node) -> dict:
    return SourceNodePayload.from_node(node).to_api_dict()


def _trace_payload(trace: WorkflowTrace | None) -> dict:
    return trace.to_dict() if trace is not None else {}


def _route_only_trace(route: RouteResult, query: str, quality: str = "skipped") -> dict:
    return WorkflowTrace(
        route=_route_payload(route),
        original_query=query,
        retrieval_query="",
        quality={"quality": quality, "reason": "non knowledge-base route"},
        retry_count=0,
    ).to_dict()


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def _merge_trace_timings(trace: dict, extra: dict) -> dict:
    timings = dict(trace.get("timings") or {})
    for key, value in extra.items():
        if value is None:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if key == "router_ms":
            # The workflow route step often reuses an already computed route,
            # so its inner timing can be 0ms. Prefer the outer controller timing.
            if numeric_value > 0:
                timings[key] = numeric_value
            continue
        timings[key] = numeric_value

    trace["timings"] = {
        key: value
        for key, value in timings.items()
        if (key == "total_ms" and float(value or 0) > 0) or (key != "total_ms" and float(value or 0) > 0)
    }
    return trace


def _retrieval_timing_key(name: str) -> str:
    lowered = name.lower()
    if "rerank" in lowered:
        return "rerank_ms"
    if "automerging" in lowered or "auto_merging" in lowered:
        return "auto_merge_ms"
    if "parentcontext" in lowered or "parent_context" in lowered:
        return "parent_context_ms"
    return f"{re.sub(r'[^a-z0-9]+', '_', lowered).strip('_')}_ms"


def _is_rerank_postprocessor(name: str) -> bool:
    return "rerank" in name.lower()


def _should_use_simple_rag_fast_path(decision: AgentDecision, grounding_mode: str) -> bool:
    route = decision.route
    return (
        decision.mode == AgentMode.RAG_WORKFLOW
        and route.query_strategy == QueryStrategy.DIRECT
        and str(route.complexity or "").lower() in {"simple", "single_hop"}
        and (grounding_mode or "off").lower() != "reflection"
    )


def _summarize_retrieval_profiles(profiles: list[dict]) -> tuple[dict, dict]:
    timing_totals: dict[str, float] = {}
    for profile in profiles:
        timing_totals["retriever_core_ms"] = timing_totals.get("retriever_core_ms", 0.0) + float(profile.get("retriever_core_ms") or 0.0)
        timing_totals["retrieval_profiled_ms"] = timing_totals.get("retrieval_profiled_ms", 0.0) + float(profile.get("retrieval_total_profiled_ms") or profile.get("retriever_core_ms") or 0.0)
        for item in profile.get("postprocessors") or []:
            key = str(item.get("timing_key") or "")
            if key:
                timing_totals[key] = timing_totals.get(key, 0.0) + float(item.get("duration_ms") or 0.0)

    profile_payload = {
        "calls": profiles,
        "call_count": len(profiles),
        "fallback_count": sum(1 for profile in profiles if profile.get("fallback")),
        "cache": {
            "enabled": any((profile.get("cache") or {}).get("enabled") for profile in profiles),
            "hit_count": sum(1 for profile in profiles if (profile.get("cache") or {}).get("hit")),
            "miss_count": sum(
                1
                for profile in profiles
                if (profile.get("cache") or {}).get("enabled") and not (profile.get("cache") or {}).get("hit")
            ),
        },
        "fallback_reasons": [
            reason
            for profile in profiles
            for reason in (profile.get("fallback_reasons") or [])
        ],
    }
    return timing_totals, profile_payload


def _step_payload(
    key: str,
    title: str,
    status: str,
    summary: str = "",
    duration_ms: float | None = None,
    items: list[dict] | None = None,
) -> dict:
    return {
        "key": key,
        "title": title,
        "status": status,
        "summary": summary,
        "duration_ms": duration_ms,
        "items": items or [],
    }


def _sse_event(event: str, data) -> str:
    if isinstance(data, str):
        payload = data
    else:
        payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _memory_service() -> MemoryService:
    service = getattr(app.state, "memory_service", None)
    if service is None:
        service = MemoryService(getattr(app.state, "redis", None))
        app.state.memory_service = service
    return service


def _long_term_memory_service() -> LongTermMemoryService:
    service = getattr(app.state, "long_term_memory_service", None)
    if service is None:
        service = LongTermMemoryService()
        app.state.long_term_memory_service = service
    return service


def _tool_registry() -> ToolRegistry:
    registry = getattr(app.state, "tool_registry", None)
    if registry is None:
        registry = build_default_tool_registry()
        app.state.tool_registry = registry
    return registry


def _handle_tool_call_decision(decision, query: str, current_user: User, route_ms: float) -> tuple[str, dict]:
    trace = _route_only_trace(decision.route, query, quality="tool_call")
    trace["controller_decision"] = _controller_decision_payload(decision)
    trace["timings"] = {"router_ms": route_ms}

    tool_plan = decision.tool_plan
    if tool_plan is None:
        answer = "当前问题已进入工具调用链路，但 AgentController 没有生成工具计划。"
        trace["tool_plan"] = {"error": "missing_tool_plan"}
        return answer, trace

    trace["tool_plan"] = {
        "method": tool_plan.method,
        "tool_name": tool_plan.tool_name,
        "arguments": tool_plan.arguments,
        "error": tool_plan.error,
    }

    if not tool_plan.has_tool_call:
        answer = f"当前问题已进入工具调用链路，但 Tool Planner 没有生成有效工具调用：{tool_plan.error}"
        return answer, trace

    tool_result = _tool_registry().call(tool_plan.tool_name, tool_plan.arguments, user=current_user)
    trace["tool_call"] = {
        "tool_name": tool_result.tool_name,
        "success": tool_result.success,
        "error": tool_result.error,
        "duration_ms": tool_result.duration_ms,
        "raw": tool_result.raw,
    }

    if tool_result.success:
        return tool_result.content, trace

    answer = (
        f"当前系统已通过 Function Calling 选择工具 {tool_plan.tool_name}，"
        f"但工具暂不可用：{tool_result.error}。"
        "后续接入真实 MCP/API Adapter 后即可完成。"
    )
    return answer, trace


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/query")
async def query_rag(request: QueryRequest):
    query_engine = getattr(app.state, "query_engine", None)
    if query_engine is None:
        return {"error": "RAG query engine is not initialized."}, 500

    print(f"\n🔍 【单次查询】问题：{request.question}")
    response = query_engine.query(request.question)

    source_nodes_data = []
    if response.source_nodes:
        for node in response.source_nodes:
            source_nodes_data.append(_source_node_payload(node))
    return {"answer": response.response, "source_nodes": source_nodes_data}


def _safe_upload_filename(filename: str) -> str:
    name = Path(filename or "").name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="filename is required")
    name = re.sub(r"[\x00-\x1f]", "", name)
    name = name.replace("\\", "_").replace("/", "_")
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_UPLOAD_EXTS:
        allowed = ", ".join(sorted(SUPPORTED_UPLOAD_EXTS))
        raise HTTPException(status_code=400, detail=f"unsupported file type: {suffix}; allowed: {allowed}")
    return name


def _save_upload_file(filename: str, content: bytes) -> dict:
    if not content:
        raise HTTPException(status_code=400, detail=f"{filename} is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"{filename} exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")

    docs_dir = Path(app_settings.docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)

    target_path = docs_dir / filename
    existed = target_path.exists()
    target_path.write_bytes(content)

    return {
        "file_name": filename,
        "path": str(target_path).replace("\\", "/"),
        "file_size": len(content),
        "overwritten": existed,
    }


def _run_incremental_ingestion_and_refresh() -> dict:
    if not _ingest_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="another ingestion task is running")

    try:
        state_path = os.path.join(app_settings.index_dir, "ingest_state.json")
        state = load_state(state_path)
        diff_dict, next_state = diff_docs(app_settings.docs_dir, state)

        changed = {
            "added": len(diff_dict.get("added", [])),
            "updated": len(diff_dict.get("updated", [])),
            "deleted": len(diff_dict.get("deleted", [])),
        }
        if not any(changed.values()):
            return {"changed": changed, "indexed": False}

        index = getattr(app.state, "index", None)
        if index is None:
            raise RuntimeError("RAG index is not initialized")

        apply_diff_to_milvus(
            index=index,
            diff_dict=diff_dict,
            chunk_size=app_settings.chunk_size,
            chunk_overlap=app_settings.chunk_overlap,
        )
        save_state(state_path, next_state)

        index, retriever, reranker, query_engine = build_rag_components()
        app.state.index = index
        app.state.retriever = retriever
        app.state.reranker = reranker
        app.state.query_engine = query_engine

        return {"changed": changed, "indexed": True}
    finally:
        _ingest_lock.release()


def _load_latest_ingestion_report() -> Optional[dict]:
    report_path = Path(app_settings.audit_dir) / "ingestion_report_latest.json"
    if not report_path.exists():
        return None
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _redis_or_503() -> redis.Redis:
    r = getattr(app.state, "redis", None)
    if r is None:
        raise HTTPException(status_code=503, detail="redis is not available")
    return r


def _save_ingest_job(r: redis.Redis, job_id: str, payload: dict) -> None:
    payload = {**payload, "updated_at": time.time()}
    r.setex(_ingest_job_key(job_id), INGEST_JOB_TTL, json.dumps(payload, ensure_ascii=False))


def _load_ingest_job(r: redis.Redis, job_id: str) -> Optional[dict]:
    raw = r.get(_ingest_job_key(job_id))
    if not raw:
        return None
    return json.loads(raw)


def _run_ingest_job(job_id: str) -> None:
    r = getattr(app.state, "redis", None)
    if r is None:
        return

    current = _load_ingest_job(r, job_id) or {}
    _save_ingest_job(r, job_id, {**current, "status": "running", "error": None})

    try:
        result = _run_incremental_ingestion_and_refresh()
        report = _load_latest_ingestion_report()
        current = _load_ingest_job(r, job_id) or {}
        _save_ingest_job(
            r,
            job_id,
            {
                **current,
                "status": "succeeded",
                "result": result,
                "report": report,
                "error": None,
            },
        )
    except Exception as e:
        report = _load_latest_ingestion_report()
        current = _load_ingest_job(r, job_id) or {}
        _save_ingest_job(
            r,
            job_id,
            {
                **current,
                "status": "failed",
                "result": None,
                "report": report,
                "error": str(e),
            },
        )


@app.post("/documents/upload")
async def upload_documents(
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    admin: User = Depends(require_admin),
):
    if not files:
        raise HTTPException(status_code=400, detail="at least one file is required")

    r = _redis_or_503()
    saved_files = []
    for file in files:
        filename = _safe_upload_filename(file.filename or "")
        content = await file.read()
        saved_files.append(await run_in_threadpool(_save_upload_file, filename, content))

    job_id = uuid.uuid4().hex
    _save_ingest_job(
        r,
        job_id,
        {
            "job_id": job_id,
            "status": "pending",
            "created_at": time.time(),
            "saved_files": saved_files,
            "result": None,
            "report": None,
            "error": None,
        },
    )
    background_tasks.add_task(_run_ingest_job, job_id)

    return {
        "ok": True,
        "job_id": job_id,
        "status": "pending",
        "saved_files": saved_files,
    }


@app.get("/documents/jobs/{job_id}")
async def get_ingest_job(
    job_id: str,
    admin: User = Depends(require_admin),
):
    r = _redis_or_503()
    job = _load_ingest_job(r, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ingestion job not found")
    return job


@app.get("/sessions")
async def list_sessions(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前用户的会话列表"""
    sessions = list_user_sessions(db, current_user.id, limit)
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "title": s.title,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
            }
            for s in sessions
        ]
    }


@app.post("/sessions")
async def create_session(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建新会话"""
    session_id = uuid.uuid4().hex
    title = "新会话"
    session = create_chat_session(db, session_id, current_user.id, title)
    return {
        "session_id": session.session_id,
        "title": session.title,
        "created_at": session.created_at.isoformat() if session.created_at else None,
    }


@app.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除会话（需验证归属）"""
    session = get_chat_session(db, session_id)
    if not session or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    soft_delete_session(db, session)
    _memory_service().delete(session_id)
    return {"ok": True}


class UpdateSessionTitleRequest(BaseModel):
    title: str


@app.put("/sessions/{session_id}/title")
async def update_session_title(
    session_id: str,
    request: UpdateSessionTitleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新会话标题（需验证归属）"""
    session = get_chat_session(db, session_id)
    if not session or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    
    session.title = title
    session.updated_at = datetime.utcnow()
    db.commit()
    
    return {"ok": True, "title": session.title}


@app.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取会话消息（需验证归属）"""
    session = get_chat_session(db, session_id)
    if not session or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    persisted_messages = list_chat_messages(db, session_id)
    if persisted_messages:
        messages = []
        for message in persisted_messages:
            row = {"role": message.role, "content": message.content}
            metadata = message.metadata_json or {}
            if message.role == "assistant":
                row["sources"] = metadata.get("sources", [])
                row["citations"] = metadata.get("citations", [])
                row["route"] = metadata.get("route")
                row["trace"] = metadata.get("trace")
            messages.append(row)
        return {"session_id": session_id, "messages": messages}

    return {"session_id": session_id, "messages": _memory_service().messages(session_id)}


@app.get("/debug/memory/{session_id}")
async def debug_memory(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Development-only memory snapshot for validating summary compression."""

    session = get_chat_session(db, session_id)
    if not session or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")

    return _memory_service().debug_snapshot(session_id, db=db)


@app.post("/debug/memory/{session_id}/compress")
async def debug_compress_memory(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Development-only endpoint to manually trigger session summary compression."""

    session = get_chat_session(db, session_id)
    if not session or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")

    memory_service = _memory_service()
    before = memory_service.debug_snapshot(session_id, db=db)
    plan_before = memory_service.compression_plan(session_id, db=db)
    memory = memory_service.load(session_id)
    try:
        compressed = memory_service.maybe_compress(
            session_id,
            memory,
            db=db,
            owner_user_id=current_user.id,
        )
        if compressed:
            memory_service.save(session_id, memory)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"memory compression failed: {exc}") from exc

    after = memory_service.debug_snapshot(session_id, db=db)
    plan_after = memory_service.compression_plan(session_id, db=db)
    return {
        "session_id": session_id,
        "compressed": compressed,
        "skip_reason": _memory_compression_skip_reason(compressed, plan_before, after),
        "plan_before": plan_before,
        "plan_after": plan_after,
        "before": {
            "postgres_message_count": before["postgres"]["message_count"],
            "summary_exists": before["summary"]["exists"],
            "redis_memory_message_count": before["redis"]["memory_message_count"],
        },
        "after": {
            "postgres_message_count": after["postgres"]["message_count"],
            "summary_exists": after["summary"]["exists"],
            "covered_until_message_id": after["summary"]["covered_until_message_id"],
            "covered_message_count": after["summary"]["covered_message_count"],
            "redis_memory_message_count": after["redis"]["memory_message_count"],
            "used_summary": after["prompt_context"]["used_summary"],
        },
        "summary": after["summary"],
    }


@app.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_start = time.perf_counter()
    print(f"💬 [ChatStream] received | user={current_user.id} | session={request.session_id} | message={request.message[:80]}")
    if not request.session_id:
        return {"error": "session_id is required."}, 400
    if not request.message:
        return {"error": "message is required."}, 400

    # 确保会话存在并属于当前用户
    session = get_chat_session(db, request.session_id)
    if not session:
        # 首次对话，创建会话记录，用用户消息作为标题
        title = request.message[:50] + ("..." if len(request.message) > 50 else "")
        session = create_chat_session(db, request.session_id, current_user.id, title)
    elif session.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问此会话")
    else:
        # 如果会话标题仍是默认值，更新为用户消息
        if not session.title or session.title == "新会话":
            new_title = request.message[:50] + ("..." if len(request.message) > 50 else "")
            session.title = new_title
            session.updated_at = datetime.utcnow()
            db.commit()
        update_session_last_message(db, session)

    route_start = time.perf_counter()
    print("🧭 [ChatStream] controller deciding...")
    decision = AgentController(_tool_registry()).decide(request.message)
    route = decision.route
    route_ms = _elapsed_ms(route_start)
    print(
        f"✅ [ChatStream] controller done | mode={decision.mode.value} | "
        f"intent={route.intent.value} | strategy={route.query_strategy.value} | {route_ms}ms"
    )
    route_payload = _route_payload(route)
    controller_payload = _controller_decision_payload(decision)
    route_event_payload = {**route_payload, "controller_decision": controller_payload}
    route_json = json.dumps(route_event_payload, ensure_ascii=False)
    memory_service = _memory_service()
    print("🧠 [ChatStream] loading memory context...")
    memory_start = time.perf_counter()
    memory = memory_service.load(request.session_id)
    memory_context = memory_service.build_context(request.session_id, memory, db=db)
    long_term_memory_service = _long_term_memory_service()
    if decision.memory_policy.use_long_term_memory:
        memory_context.long_term_memories = long_term_memory_service.search(
            user_id=current_user.id,
            query=request.message,
            limit=decision.memory_policy.long_term_top_k,
        )
    memory_ms = _elapsed_ms(memory_start)
    print("✅ [ChatStream] memory context ready")
    if decision.mode == AgentMode.CLARIFICATION:
        answer = decision.clarification.question or "请补充更多信息后我再继续。"
        trace = _route_only_trace(route, request.message, quality="clarification")
        trace["controller_decision"] = controller_payload
        trace["timings"] = {
            "router_ms": route_ms,
            "memory_ms": memory_ms,
            "total_ms": _elapsed_ms(request_start),
        }
        trace_json = json.dumps(trace, ensure_ascii=False)
        save_start = time.perf_counter()
        memory_service.append_exchange_with_metadata(
            request.session_id,
            memory,
            request.message,
            answer,
            route=route_payload,
            trace=trace,
            sources=[],
            citations=[],
            db=db,
            owner_user_id=current_user.id,
        )
        memory_service.save(request.session_id, memory)
        _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)

        def clarification_gen():
            yield f"event: route\ndata: {route_json}\n\n"
            yield _sse_event(
                "step",
                _step_payload("clarification", "2. Clarification", "done", "需要用户补充关键信息"),
            )
            yield f"event: trace\ndata: {trace_json}\n\n"
            yield f"event: chunk\ndata: {answer}\n\n"
            payload = json.dumps(
                {
                    "route": route_payload,
                    "controller_decision": controller_payload,
                    "trace": trace,
                    "source_nodes": [],
                    "citations": [],
                },
                ensure_ascii=False,
            )
            yield f"event: result\ndata: {payload}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(clarification_gen(), media_type="text/event-stream")

    if decision.mode == AgentMode.CHITCHAT:
        trace = _route_only_trace(route, request.message, quality="chitchat_fast_path")
        trace["controller_decision"] = controller_payload
        trace["timings"] = {
            "router_ms": route_ms,
            "memory_ms": memory_ms,
            "total_ms": _elapsed_ms(request_start),
        }
        trace["generation"] = {
            "mode": "lightweight_chat",
            "used_memory_context": memory_context.has_context,
            "used_summary": memory_context.used_summary,
            "recent_message_count": len(memory_context.recent_messages),
            "long_term_memory_count": len(memory_context.long_term_memories),
        }
        trace_json = json.dumps(trace, ensure_ascii=False)

        def chitchat_gen():
            yield f"event: route\ndata: {route_json}\n\n"
            yield _sse_event(
                "step",
                _step_payload(
                    "router",
                    "1. Query Router",
                    "done",
                    f"路由完成：{route.intent.value}",
                    route_ms,
                    [
                        {"label": "intent", "value": route.intent.value},
                        {"label": "method", "value": route.method},
                    ],
                ),
            )
            yield _sse_event(
                "step",
                _step_payload("generation", "2. Lightweight Chat", "running", "正在使用会话记忆生成轻量回答"),
            )
            yield f"event: trace\ndata: {trace_json}\n\n"

            answer_parts = []
            generation_start = time.perf_counter()
            try:
                for token in _stream_lightweight_chat(request.message, memory_context):
                    answer_parts.append(token)
                    safe_token = token.replace("\n", "\\n")
                    yield f"event: chunk\ndata: {safe_token}\n\n"
            except Exception as exc:
                answer_parts = [f"轻量聊天生成失败：{exc}"]
                trace["generation"]["error"] = str(exc)
                yield _sse_event("error", {"message": str(exc)})
                yield f"event: chunk\ndata: {answer_parts[0]}\n\n"

            final_answer = "".join(answer_parts).strip() or "我已经收到。"
            trace.setdefault("timings", {})["generation_ms"] = _elapsed_ms(generation_start)
            trace.setdefault("timings", {})["memory_ms"] = memory_ms
            trace.setdefault("timings", {})["total_ms"] = _elapsed_ms(request_start)
            trace_json_done = json.dumps(trace, ensure_ascii=False)
            memory_service.append_exchange_with_metadata(
                request.session_id,
                memory,
                request.message,
                final_answer,
                route=route_payload,
                trace=trace,
                sources=[],
                citations=[],
                db=db,
                owner_user_id=current_user.id,
            )
            memory_service.save(request.session_id, memory)
            _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)
            _schedule_long_term_memory_add(
                background_tasks,
                long_term_memory_service,
                current_user.id,
                request.message,
                final_answer,
            )
            yield f"event: trace\ndata: {trace_json_done}\n\n"
            yield _sse_event(
                "step",
                _step_payload(
                    "generation",
                    "2. Lightweight Chat",
                    "done",
                    "轻量回答生成完成",
                    trace["timings"]["generation_ms"],
                ),
            )
            payload = json.dumps(
                {
                    "route": route_payload,
                    "controller_decision": controller_payload,
                    "trace": trace,
                    "source_nodes": [],
                    "citations": [],
                },
                ensure_ascii=False,
            )
            yield f"event: result\ndata: {payload}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(chitchat_gen(), media_type="text/event-stream")

    if decision.mode == AgentMode.TOOL_CALL:
        answer, trace = _handle_tool_call_decision(decision, request.message, current_user, route_ms)
        trace_json = json.dumps(trace, ensure_ascii=False)
        memory_service.append_exchange_with_metadata(
            request.session_id,
            memory,
            request.message,
            answer,
            route=route_payload,
            trace=trace,
            sources=[],
            citations=[],
            db=db,
            owner_user_id=current_user.id,
        )
        memory_service.save(request.session_id, memory)
        _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)

        def tool_call_gen():
            yield f"event: route\ndata: {route_json}\n\n"
            yield _sse_event(
                "step",
                _step_payload(
                    "tool_call",
                    "2. Tool Call",
                    "done",
                    "工具调用链路处理完成",
                    trace.get("tool_call", {}).get("duration_ms"),
                ),
            )
            yield f"event: trace\ndata: {trace_json}\n\n"
            yield f"event: chunk\ndata: {answer}\n\n"
            payload = json.dumps(
                {
                    "route": route_payload,
                    "controller_decision": controller_payload,
                    "trace": trace,
                    "source_nodes": [],
                },
                ensure_ascii=False,
            )
            yield f"event: result\ndata: {payload}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(tool_call_gen(), media_type="text/event-stream")

    index = getattr(app.state, "index", None)
    retriever = getattr(app.state, "retriever", None)
    reranker = getattr(app.state, "reranker", None)

    if index is None or retriever is None or reranker is None:
        memory_service.append_exchange_with_metadata(
            request.session_id,
            memory,
            request.message,
            "RAG components are not initialized.",
            assistant_status="error",
            route=None,
            trace=None,
            sources=[],
            citations=[],
            db=db,
            owner_user_id=current_user.id,
        )
        memory_service.save(request.session_id, memory)
        _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)
        return {"error": "RAG components are not initialized."}, 500

    print(f"\n[Stream Chat] session_id={request.session_id}")

    async def gen():
        query_engine = getattr(app.state, "query_engine", None)
        base_retriever = getattr(app.state, "retriever", None)
        retrieval_cache = RetrievalCache(getattr(app.state, "redis", None))
        retrieval_profiles: list[dict] = []
        if query_engine is None:
            memory_service.append_exchange_with_metadata(
                request.session_id,
                memory,
                request.message,
                "RAG query engine is not initialized.",
                assistant_status="error",
                route=None,
                trace=None,
                sources=[],
                citations=[],
                db=db,
                owner_user_id=current_user.id,
            )
            memory_service.save(request.session_id, memory)
            _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)
            yield _sse_event("error", {"message": "RAG query engine is not initialized."})
            yield "data: [DONE]\n\n"
            return

        def _profiled_retrieve(query: str) -> tuple[list, dict]:
            bundle = QueryBundle(query)
            profile = {
                "query": query,
                "fallback": False,
                "fallback_reasons": [],
                "retriever_node_count": 0,
                "final_node_count": 0,
                "postprocessors": [],
            }
            total_start = time.perf_counter()
            core_start = time.perf_counter()
            nodes = base_retriever.retrieve(bundle)
            profile["retriever_core_ms"] = _elapsed_ms(core_start)
            profile["retriever_node_count"] = len(nodes)

            for postprocessor in getattr(query_engine, "_node_postprocessors", []) or []:
                name = postprocessor.__class__.__name__
                before_nodes = nodes
                if _is_rerank_postprocessor(name):
                    if not app_settings.rerank_enabled:
                        profile["postprocessors"].append(
                            {
                                "name": name,
                                "timing_key": _retrieval_timing_key(name),
                                "duration_ms": 0,
                                "node_count": len(nodes),
                                "status": "skipped",
                                "skip_reason": "rerank_disabled",
                            }
                        )
                        continue
                    if len(nodes) < app_settings.rerank_min_candidates:
                        profile["postprocessors"].append(
                            {
                                "name": name,
                                "timing_key": _retrieval_timing_key(name),
                                "duration_ms": 0,
                                "node_count": len(nodes),
                                "status": "skipped",
                                "skip_reason": "candidate_count_below_threshold",
                                "min_candidates": app_settings.rerank_min_candidates,
                            }
                        )
                        continue

                post_start = time.perf_counter()
                try:
                    if _is_rerank_postprocessor(name):
                        post_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                        post_future = post_executor.submit(
                            postprocessor.postprocess_nodes,
                            nodes,
                            query_bundle=bundle,
                        )
                        try:
                            nodes = post_future.result(timeout=app_settings.rerank_timeout_seconds)
                        finally:
                            post_executor.shutdown(wait=False, cancel_futures=True)
                    else:
                        nodes = postprocessor.postprocess_nodes(nodes, query_bundle=bundle)
                except concurrent.futures.TimeoutError:
                    duration_ms = _elapsed_ms(post_start)
                    profile["fallback"] = True
                    profile["fallback_reasons"].append(f"{name}: timeout after {app_settings.rerank_timeout_seconds}s")
                    profile["postprocessors"].append(
                        {
                            "name": name,
                            "timing_key": _retrieval_timing_key(name),
                            "duration_ms": duration_ms,
                            "node_count": len(before_nodes),
                            "status": "timeout_fallback",
                            "error": f"timeout after {app_settings.rerank_timeout_seconds}s",
                        }
                    )
                    nodes = before_nodes
                    print(f"[Retrieval] postprocessor timeout, skipped {name}: {app_settings.rerank_timeout_seconds}s")
                    continue
                except Exception as exc:
                    duration_ms = _elapsed_ms(post_start)
                    profile["fallback"] = True
                    profile["fallback_reasons"].append(f"{name}: {exc}")
                    profile["postprocessors"].append(
                        {
                            "name": name,
                            "timing_key": _retrieval_timing_key(name),
                            "duration_ms": duration_ms,
                            "node_count": len(before_nodes),
                            "status": "failed_fallback",
                            "error": str(exc),
                        }
                    )
                    nodes = before_nodes
                    print(f"[Retrieval] postprocessor failed, skipped {name}: {exc}")
                    continue

                duration_ms = _elapsed_ms(post_start)
                profile["postprocessors"].append(
                    {
                        "name": name,
                        "timing_key": _retrieval_timing_key(name),
                        "duration_ms": duration_ms,
                        "node_count": len(nodes),
                        "status": "ok",
                    }
                )

            profile["final_node_count"] = len(nodes)
            profile["retrieval_total_profiled_ms"] = _elapsed_ms(total_start)
            return nodes, profile

        def retrieve_for_workflow(query: str, top_k: int) -> list:
            cached_nodes, cache_metadata = retrieval_cache.get(query, top_k=top_k)
            if cached_nodes is not None:
                retrieval_profiles.append(
                    {
                        "query": query,
                        "cache": cache_metadata,
                        "fallback": False,
                        "fallback_reasons": [],
                        "retriever_core_ms": cache_metadata.get("read_ms", 0),
                        "retriever_node_count": len(cached_nodes),
                        "final_node_count": len(cached_nodes),
                        "postprocessors": [],
                        "retrieval_total_profiled_ms": cache_metadata.get("read_ms", 0),
                    }
                )
                print(f"[RetrievalCache] HIT query={query[:80]} nodes={len(cached_nodes)}")
                return cached_nodes

            # Prefer a profiled retrieval pipeline so we can split core
            # retriever, rerank, and parent-context/auto-merge timings.
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            future = executor.submit(_profiled_retrieve, query)
            try:
                nodes, profile = future.result(timeout=WORKFLOW_RETRIEVAL_TIMEOUT_SECONDS)
                cache_write = retrieval_cache.set(query, top_k=top_k, nodes=nodes)
                profile["cache"] = {**cache_metadata, **cache_write, "hit": False}
                retrieval_profiles.append(profile)
                return nodes
            except concurrent.futures.TimeoutError:
                future.cancel()
                print(f"[Retrieval] profiled retrieve timeout after {WORKFLOW_RETRIEVAL_TIMEOUT_SECONDS}s, fallback to base retriever: {query}")
            except Exception as exc:
                print(f"[Retrieval] profiled retrieve failed, fallback to base retriever: {exc}")
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

            if base_retriever is None:
                return []
            try:
                fallback_start = time.perf_counter()
                nodes = base_retriever.retrieve(QueryBundle(query))
                retrieval_profiles.append(
                    {
                        "query": query,
                        "fallback": True,
                        "fallback_reasons": ["profiled_retrieve_timeout_or_error"],
                        "retriever_core_ms": _elapsed_ms(fallback_start),
                        "retriever_node_count": len(nodes),
                        "final_node_count": len(nodes),
                        "postprocessors": [],
                        "retrieval_total_profiled_ms": _elapsed_ms(fallback_start),
                    }
                )
                return nodes
            except Exception as exc:
                print(f"[Retrieval] base retriever failed: {exc}")
                return []

        grounding_mode = (request.grounding_mode or "off").lower()
        if _should_use_simple_rag_fast_path(decision, grounding_mode):
            yield f"event: route\ndata: {route_json}\n\n"
            yield _sse_event(
                "step",
                _step_payload(
                    "retrieval",
                    "2. Fast Retrieval",
                    "running",
                    "简单问题走 Fast RAG Path，跳过完整 Agentic Workflow",
                ),
            )
            fast_start = time.perf_counter()
            source_node_objects = retrieve_for_workflow(request.message, app_settings.top_k)
            retrieval_ms = _elapsed_ms(fast_start)
            retrieval_timing_totals, retrieval_profile_payload = _summarize_retrieval_profiles(retrieval_profiles)
            trace_payload = _route_only_trace(route, request.message, quality="simple_rag_fast_path")
            trace_payload["controller_decision"] = controller_payload
            trace_payload["retrieval_query"] = request.message
            trace_payload["retrieval_profile"] = retrieval_profile_payload
            trace_payload["generation"] = {
                "mode": "simple_rag_fast_path",
                "skipped_workflow": True,
                "node_count": len(source_node_objects),
            }
            trace_payload = _merge_trace_timings(
                trace_payload,
                {
                    "router_ms": route_ms,
                    "memory_ms": memory_ms,
                    "retrieval_ms": retrieval_ms,
                    **retrieval_timing_totals,
                },
            )
            yield _sse_event(
                "step",
                _step_payload(
                    "retrieval",
                    "2. Fast Retrieval",
                    "done",
                    f"Fast Path 检索完成，召回 {len(source_node_objects)} 个候选节点",
                    retrieval_ms,
                    [{"label": "nodes", "value": str(len(source_node_objects))}],
                ),
            )
            yield f"event: trace\ndata: {json.dumps(trace_payload, ensure_ascii=False)}\n\n"

            yield _sse_event("step", _step_payload("generation", "3. Generation", "running", "正在基于检索结果生成回答"))
            generation_start = time.perf_counter()
            source_nodes = [_source_node_payload(node) for node in source_node_objects]
            citations = build_citations_from_nodes(source_node_objects)
            answer_parts = []
            try:
                for token in stream_answer_from_nodes(
                    request.message,
                    source_node_objects,
                    memory_context=memory_context,
                    intermediate_answers=[],
                ):
                    answer_parts.append(token)
                    yield f"event: chunk\ndata: {token.replace(chr(10), '\\n')}\n\n"
            except Exception as exc:
                memory_service.append_exchange_with_metadata(
                    request.session_id,
                    memory,
                    request.message,
                    f"Answer generation failed: {exc}",
                    assistant_status="error",
                    route=route_payload,
                    trace=trace_payload,
                    sources=source_nodes,
                    citations=citations,
                    db=db,
                    owner_user_id=current_user.id,
                )
                memory_service.save(request.session_id, memory)
                _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)
                yield _sse_event("error", {"message": f"Answer generation failed: {exc}"})
                yield "data: [DONE]\n\n"
                return

            final_answer = "".join(answer_parts).strip()
            generation_ms = _elapsed_ms(generation_start)
            trace_payload = _merge_trace_timings(trace_payload, {"generation_ms": generation_ms})
            trace_payload["generation"] = {
                **trace_payload.get("generation", {}),
                "citation_count": len(citations),
            }
            yield _sse_event("step", _step_payload("generation", "3. Generation", "done", "回答生成完成", generation_ms))

            save_start = time.perf_counter()
            memory_service.append_exchange_with_metadata(
                request.session_id,
                memory,
                request.message,
                final_answer,
                route=route_payload,
                trace=trace_payload,
                sources=source_nodes,
                citations=citations,
                db=db,
                owner_user_id=current_user.id,
            )
            memory_service.save(request.session_id, memory)
            _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)
            _schedule_long_term_memory_add(
                background_tasks,
                long_term_memory_service,
                current_user.id,
                request.message,
                final_answer,
            )
            trace_payload = _merge_trace_timings(
                trace_payload,
                {
                    "memory_save_ms": _elapsed_ms(save_start),
                    "total_ms": _elapsed_ms(request_start),
                },
            )
            yield f"event: trace\ndata: {json.dumps(trace_payload, ensure_ascii=False)}\n\n"
            payload = json.dumps(
                {
                    "route": route_payload,
                    "controller_decision": controller_payload,
                    "trace": trace_payload,
                    "source_nodes": source_nodes,
                    "citations": citations,
                },
                ensure_ascii=False,
            )
            yield f"event: result\ndata: {payload}\n\n"
            yield "data: [DONE]\n\n"
            return

        workflow = AgenticRAGWorkflow(
            retrieve_fn=retrieve_for_workflow,
            route_fn=lambda _: route,
            max_retry=decision.max_retries,
            initial_top_k=5,
        )
        workflow_start = time.perf_counter()
        handler = workflow.run(query=request.message)

        yield f"event: route\ndata: {route_json}\n\n"
        yield _sse_event("status", "正在执行 LlamaIndex Workflow...")

        try:
            async for event in handler.stream_events():
                if isinstance(event, WorkflowStepEvent):
                    yield _sse_event("step", event.to_payload())

            workflow_result = await handler
        except asyncio.CancelledError:
            memory_service.append_exchange_with_metadata(
                request.session_id,
                memory,
                request.message,
                "Answer interrupted by client.",
                assistant_status="interrupted",
                route=route_payload,
                trace=trace_payload if 'trace_payload' in dir() else None,
                sources=source_nodes if 'source_nodes' in dir() else [],
                citations=citations if 'citations' in dir() else [],
                db=db,
                owner_user_id=current_user.id,
            )
            memory_service.save(request.session_id, memory)
            _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)
            raise
        except Exception as exc:
            memory_service.append_exchange_with_metadata(
                request.session_id,
                memory,
                request.message,
                f"Agentic workflow failed: {exc}",
                assistant_status="error",
                route=route_payload,
                trace=trace_payload if 'trace_payload' in dir() else None,
                sources=source_nodes if 'source_nodes' in dir() else [],
                citations=citations if 'citations' in dir() else [],
                db=db,
                owner_user_id=current_user.id,
            )
            memory_service.save(request.session_id, memory)
            _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)
            yield _sse_event("error", {"message": f"Agentic workflow failed: {exc}"})
            yield "data: [DONE]\n\n"
            return

        if workflow_result is None:
            memory_service.append_exchange_with_metadata(
                request.session_id,
                memory,
                request.message,
                "Agentic workflow returned no result.",
                assistant_status="error",
                route=route_payload,
                trace=None,
                sources=[],
                citations=[],
                db=db,
                owner_user_id=current_user.id,
            )
            memory_service.save(request.session_id, memory)
            _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)
            yield _sse_event("error", {"message": "Agentic workflow returned no result."})
            yield "data: [DONE]\n\n"
            return

        retrieval_query = workflow_result.retrieval_query or request.message
        trace_payload = workflow_result.to_trace_dict()
        trace_payload["controller_decision"] = controller_payload
        retrieval_timing_totals, retrieval_profile_payload = _summarize_retrieval_profiles(retrieval_profiles)
        if retrieval_profiles:
            trace_payload["retrieval_profile"] = retrieval_profile_payload
        trace_payload = _merge_trace_timings(
            trace_payload,
            {
                "router_ms": route_ms,
                "memory_ms": memory_ms,
                "workflow_ms": _elapsed_ms(workflow_start),
                **retrieval_timing_totals,
            },
        )
        yield f"event: trace\ndata: {json.dumps(trace_payload, ensure_ascii=False)}\n\n"

        yield _sse_event("step", _step_payload("generation", "5. Generation", "running", "正在基于检索结果生成回答"))
        generation_start = time.perf_counter()
        source_nodes = [_source_node_payload(node) for node in workflow_result.source_nodes]
        citations = build_citations_from_nodes(workflow_result.source_nodes)
        intermediate_answers = []
        if workflow_result.sub_question_results:
            yield _sse_event(
                "step",
                _step_payload(
                    "synthesis",
                    "5. Intermediate Synthesis",
                    "running",
                    "正在为每个子问题生成中间答案",
                ),
            )
            synthesis_start = time.perf_counter()
            try:
                intermediate_answers = synthesize_intermediate_answers(
                    workflow_result.sub_question_results,
                    memory_context=memory_context,
                )
            except Exception as exc:
                trace_payload.setdefault("decomposition", {})["intermediate_error"] = str(exc)
                yield _sse_event("error", {"message": f"Intermediate synthesis failed: {exc}"})
            else:
                synthesis_ms = _elapsed_ms(synthesis_start)
                trace_payload = _merge_trace_timings(trace_payload, {"intermediate_synthesis_ms": synthesis_ms})
                trace_payload.setdefault("decomposition", {})["intermediate_answers"] = intermediate_answers
                yield _sse_event(
                    "step",
                    _step_payload(
                        "synthesis",
                        "5. Intermediate Synthesis",
                        "done",
                        f"已生成 {len(intermediate_answers)} 个子问题中间答案",
                        synthesis_ms,
                        [
                            {
                                "label": f"q{item.get('index')}",
                                "value": str(item.get("answer") or "")[:160],
                            }
                            for item in intermediate_answers
                        ],
                    ),
                )
                yield f"event: trace\ndata: {json.dumps(trace_payload, ensure_ascii=False)}\n\n"
        answer_parts = []
        try:
            for token in stream_answer_from_nodes(
                retrieval_query,
                workflow_result.source_nodes,
                memory_context=memory_context,
                intermediate_answers=intermediate_answers,
            ):
                answer_parts.append(token)
                safe_token = token.replace("\n", "\\n")
                yield f"event: chunk\ndata: {safe_token}\n\n"
        except asyncio.CancelledError:
            partial_answer = "".join(answer_parts).strip() or "Answer interrupted by client."
            memory_service.append_exchange_with_metadata(
                request.session_id,
                memory,
                request.message,
                partial_answer,
                assistant_status="interrupted",
                route=route_payload,
                trace=trace_payload,
                sources=source_nodes,
                citations=citations,
                db=db,
                owner_user_id=current_user.id,
            )
            memory_service.save(request.session_id, memory)
            _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)
            raise
        except Exception as exc:
            fallback_answer = build_fallback_answer_from_intermediate(intermediate_answers)
            if fallback_answer:
                answer_parts = [fallback_answer]
                trace_payload["generation"] = {
                    "mode": "intermediate_fallback",
                    "error": str(exc),
                    "reused_intermediate_answers": True,
                }
                yield _sse_event(
                    "step",
                    _step_payload(
                        "generation",
                        "5. Generation",
                        "done",
                        "最终生成超时，已使用子问题中间答案兜底",
                        _elapsed_ms(generation_start),
                    ),
                )
                for line in fallback_answer.splitlines(keepends=True):
                    safe_line = line.replace("\n", "\\n")
                    yield f"event: chunk\ndata: {safe_line}\n\n"
            else:
                memory_service.append_exchange_with_metadata(
                    request.session_id,
                    memory,
                    request.message,
                    f"Answer generation failed: {exc}",
                    assistant_status="error",
                    route=route_payload,
                    trace=trace_payload,
                    sources=source_nodes,
                    citations=citations,
                    db=db,
                    owner_user_id=current_user.id,
                )
                memory_service.save(request.session_id, memory)
                _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)
                yield _sse_event("error", {"message": f"Answer generation failed: {exc}"})
                yield "data: [DONE]\n\n"
                return
        if not answer_parts:
            memory_service.append_exchange_with_metadata(
                request.session_id,
                memory,
                request.message,
                "Answer generation returned empty response.",
                assistant_status="error",
                route=route_payload,
                trace=trace_payload,
                sources=source_nodes,
                citations=citations,
                db=db,
                owner_user_id=current_user.id,
            )
            memory_service.save(request.session_id, memory)
            _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)
            yield _sse_event("error", {"message": "Answer generation returned empty response."})
            yield "data: [DONE]\n\n"
            return
        generation_mode = trace_payload.get("generation", {}).get("mode") or "nodes_synthesizer"
        generation_ms = _elapsed_ms(generation_start)
        trace_payload = _merge_trace_timings(trace_payload, {"generation_ms": generation_ms})
        trace_payload["generation"] = {
            **trace_payload.get("generation", {}),
            "mode": generation_mode,
            "reused_workflow_nodes": True,
            "node_count": len(workflow_result.source_nodes),
            "citation_count": len(citations),
        }
        yield _sse_event("step", _step_payload("generation", "5. Generation", "done", "回答生成完成", generation_ms))

        final_answer = "".join(answer_parts)
        grounding_mode = (request.grounding_mode or "off").lower()
        if grounding_mode == "reflection":
            grounding_enabled = True
            grounding_source = "forced_by_user"
        elif grounding_mode == "auto":
            grounding_enabled = decision.need_grounding
            grounding_source = "agent_controller"
        else:
            grounding_enabled = False
            grounding_source = "disabled_by_user"
        trace_payload["grounding_mode"] = grounding_mode
        trace_payload["grounding_decision"] = {
            "enabled": grounding_enabled,
            "source": grounding_source,
            "controller_need_grounding": decision.need_grounding,
        }
        if grounding_enabled and should_run_grounding(
            answer=final_answer,
            nodes=workflow_result.source_nodes,
            route=route_payload,
            quality=trace_payload.get("quality"),
        ):
            yield _sse_event(
                "step",
                _step_payload(
                    "grounding",
                    "8. Grounding Check",
                    "running",
                    "正在校验答案是否被证据支撑",
                ),
            )
            grounding_start = time.perf_counter()
            grounding_result = check_answer_grounding(
                question=request.message,
                answer=final_answer,
                nodes=workflow_result.source_nodes,
            )
            grounding_ms = _elapsed_ms(grounding_start)
            trace_payload = _merge_trace_timings(trace_payload, {"grounding_ms": grounding_ms})
            trace_payload["grounding"] = grounding_result.to_dict()
            yield _sse_event(
                "step",
                _step_payload(
                    "grounding",
                    "8. Grounding Check",
                    "done" if grounding_result.verdict == "pass" else "warn",
                    f"{grounding_result.verdict} · score={grounding_result.grounding_score:.2f}",
                    grounding_ms,
                    [
                        {"label": "summary", "value": grounding_result.summary},
                        {
                            "label": "unsupported",
                            "value": "；".join(grounding_result.unsupported_points[:3]) or "-",
                        },
                    ],
                ),
            )
            yield f"event: trace\ndata: {json.dumps(trace_payload, ensure_ascii=False)}\n\n"
        elif not grounding_enabled:
            trace_payload["grounding"] = {
                "verdict": "skipped",
                "grounding_score": None,
                "summary": "已关闭反思模式，跳过答案证据一致性校验。",
                "claims": [],
                "unsupported_points": [],
                "method": grounding_source,
                "error": "",
            }

        memory_service.append_exchange_with_metadata(
            request.session_id,
            memory,
            request.message,
            final_answer,
            route=route_payload,
            trace=trace_payload,
            sources=source_nodes,
            citations=citations,
            db=db,
            owner_user_id=current_user.id,
        )
        memory_service.save(request.session_id, memory)
        _schedule_memory_compression(background_tasks, memory_service, request.session_id, current_user.id)
        _schedule_long_term_memory_add(
            background_tasks,
            long_term_memory_service,
            current_user.id,
            request.message,
            final_answer,
        )
        trace_payload = _merge_trace_timings(
            trace_payload,
            {
                "memory_save_ms": _elapsed_ms(save_start),
                "total_ms": _elapsed_ms(request_start),
            },
        )

        payload = json.dumps(
            {
                "route": route_payload,
                "controller_decision": controller_payload,
                "trace": trace_payload,
                "source_nodes": source_nodes,
                "citations": citations,
            },
            ensure_ascii=False,
        )
        yield f"event: result\ndata: {payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")

