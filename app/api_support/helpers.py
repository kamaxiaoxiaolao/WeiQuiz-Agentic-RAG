"""Shared helper functions for route modules."""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, List, Optional

import redis
from fastapi import BackgroundTasks, HTTPException, Request
from llama_index.core.schema import QueryBundle
from sqlalchemy.orm import Session

from app.agentic.controller import AgentDecision, AgentMode
from app.agentic.node_synthesizer import format_memory_context
from app.agentic.rag_workflow import WorkflowTrace
from app.agentic.router import QueryStrategy, RouteResult
from app.api_support.state import get_app_state
from app.auth.repository import (
    create_audit_log,
    create_knowledge_job,
    get_session_factory,
    list_knowledge_documents,
    list_knowledge_jobs,
    mark_knowledge_document_deleted,
    update_knowledge_job,
    upsert_knowledge_document,
)
from app.config import settings as app_settings
from app.ingest.milvus_loader import apply_diff_to_milvus
from app.ingest.sync import diff_docs, load_state, save_state
from app.llm import LLMTask, get_llm_gateway
from app.metadata_schema import SourceNodePayload
from app.rag_milvus import build_rag_components
from app.services.long_term_memory_service import LongTermMemoryService
from app.services.memory_service import MemoryService
from app.storage.auth_models import User
from app.tools import ToolRegistry, build_default_tool_registry


SUPPORTED_UPLOAD_EXTS = {".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_ingest_lock = threading.Lock()
INGEST_JOB_TTL = 60 * 60 * 24
WORKFLOW_RETRIEVAL_TIMEOUT_SECONDS = 20


def _ingest_job_key(job_id: str) -> str:
    return f"rag:ingest:job:{job_id}"


def _docs_root() -> Path:
    return Path(app_settings.docs_dir).resolve()


def _index_state_path() -> Path:
    return Path(app_settings.index_dir) / "ingest_state.json"


def _safe_docs_relative_path(document_path: str) -> Path:
    normalized = (document_path or "").replace("\\", "/").strip("/")
    if not normalized:
        raise HTTPException(status_code=400, detail="document path is required")
    if "\x00" in normalized:
        raise HTTPException(status_code=400, detail="invalid document path")

    relative = Path(normalized)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise HTTPException(status_code=400, detail="invalid document path")
    if relative.suffix.lower() not in SUPPORTED_UPLOAD_EXTS:
        allowed = ", ".join(sorted(SUPPORTED_UPLOAD_EXTS))
        raise HTTPException(status_code=400, detail=f"unsupported file type: {relative.suffix}; allowed: {allowed}")

    docs_dir = _docs_root()
    target = (docs_dir / relative).resolve()
    if docs_dir != target and docs_dir not in target.parents:
        raise HTTPException(status_code=400, detail="invalid document path")
    return target


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


def _schedule_long_term_memory_add(
    background_tasks: BackgroundTasks,
    long_term_memory_service: LongTermMemoryService,
    user_id: str,
    user_message: str,
    assistant_answer: str,
) -> None:
    if not long_term_memory_service.should_write(user_message, assistant_answer):
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
    response = get_llm_gateway().stream_chat_completion(
        task=LLMTask.LIGHTWEIGHT_CHAT,
        messages=[
            {
                "role": "system",
                "content": "你是 WeiQuiz 的轻量会话助手，只处理普通对话和多轮记忆追问。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=app_settings.llm_lightweight_chat_max_tokens,
    )
    for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        token = getattr(delta, "content", None) if delta is not None else None
        if token:
            yield token


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


def _user_payload(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "role": user.role,
        "status": user.status,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def _client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    forwarded = request.headers.get("x-forwarded-for") if request.headers else None
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host


def _audit_payload(row) -> dict:
    return {
        "id": row.id,
        "actor_user_id": row.actor_user_id,
        "actor_username": row.actor_username,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "resource_name": row.resource_name,
        "status": row.status,
        "detail": row.detail_json or {},
        "ip_address": row.ip_address,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _write_audit_log(
    db: Session,
    *,
    actor: User,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    resource_name: str | None = None,
    status: str = "succeeded",
    detail: dict | None = None,
    request: Request | None = None,
) -> None:
    try:
        create_audit_log(
            db,
            actor_user_id=actor.id,
            actor_username=actor.username,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            status=status,
            detail_json=detail or {},
            ip_address=_client_ip(request),
        )
    except Exception as exc:
        print(f"[AuditLog] write failed action={action} resource={resource_type}:{resource_id} error={exc}")


def _source_node_payload(node) -> dict:
    return SourceNodePayload.from_node(node).to_api_dict()


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
    service = getattr(get_app_state(), "memory_service", None)
    if service is None:
        service = MemoryService(getattr(get_app_state(), "redis", None))
        get_app_state().memory_service = service
    return service


def _long_term_memory_service() -> LongTermMemoryService:
    service = getattr(get_app_state(), "long_term_memory_service", None)
    if service is None:
        service = LongTermMemoryService()
        get_app_state().long_term_memory_service = service
    return service


def _tool_registry() -> ToolRegistry:
    registry = getattr(get_app_state(), "tool_registry", None)
    if registry is None:
        registry = build_default_tool_registry()
        get_app_state().tool_registry = registry
    return registry


async def _handle_tool_call_decision(decision, query: str, current_user: User, route_ms: float) -> tuple[str, dict]:
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

    tool_result = await _tool_registry().call_async(tool_plan.tool_name, tool_plan.arguments, user=current_user)
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

        index = getattr(get_app_state(), "index", None)
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
        get_app_state().index = index
        get_app_state().retriever = retriever
        get_app_state().reranker = reranker
        get_app_state().query_engine = query_engine

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
    r = getattr(get_app_state(), "redis", None)
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


def _list_ingest_jobs(r: redis.Redis, limit: int = 20) -> list[dict]:
    jobs: list[dict] = []
    for key in r.scan_iter(_ingest_job_key("*"), count=100):
        raw = r.get(key)
        if not raw:
            continue
        try:
            jobs.append(json.loads(raw))
        except Exception:
            continue
    jobs.sort(key=lambda item: float(item.get("created_at") or item.get("updated_at") or 0), reverse=True)
    return jobs[:limit]


def _document_state_lookup() -> dict[str, dict]:
    state_path = _index_state_path()
    if not state_path.exists():
        return {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(state, dict):
        return {}

    candidates: list[Any] = []
    for key in ("docs", "documents", "files"):
        value = state.get(key)
        if isinstance(value, dict):
            candidates.extend(value.values())
        elif isinstance(value, list):
            candidates.extend(value)

    lookup: dict[str, dict] = {}
    docs_dir = _docs_root()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path") or item.get("file_path") or item.get("source") or "")
        raw_name = str(item.get("filename") or item.get("file_name") or Path(raw_path).name)
        if raw_name:
            lookup[raw_name] = item
        if raw_path:
            normalized_path = raw_path.replace("\\", "/")
            lookup[normalized_path] = item
            lookup[Path(normalized_path).name] = item
            try:
                raw_abs = Path(raw_path).resolve()
                lookup[raw_abs.relative_to(docs_dir).as_posix()] = item
            except Exception:
                pass
            try:
                lookup[Path(raw_path).relative_to(Path(app_settings.docs_dir)).as_posix()] = item
            except Exception:
                pass
    return lookup


def _document_payload(path: Path, docs_dir: Path, state_lookup: dict[str, dict]) -> dict:
    stat = path.stat()
    relative_path = path.relative_to(docs_dir).as_posix()
    state = (
        state_lookup.get(relative_path)
        or state_lookup.get(str(path).replace("\\", "/"))
        or state_lookup.get(path.name)
        or {}
    )
    indexed_at = state.get("indexed_at") or state.get("updated_at") or state.get("mtime")
    status = "indexed" if state else "pending"
    return {
        "id": relative_path,
        "relative_path": relative_path,
        "filename": path.name,
        "title": path.stem,
        "file_type": path.suffix.lower().lstrip("."),
        "file_size": stat.st_size,
        "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "status": status,
        "indexed_at": indexed_at,
        "chunk_count": int(state.get("chunk_count") or state.get("chunks") or 0),
        "metadata": state,
    }


def _serialize_knowledge_document(row) -> dict:
    return {
        "id": row.id,
        "relative_path": row.relative_path,
        "filename": row.filename,
        "title": Path(row.filename).stem,
        "file_type": str(row.file_type or "").lstrip("."),
        "file_size": row.file_size or 0,
        "status": row.indexed_status,
        "document_status": row.status,
        "indexed_at": row.last_ingested_at.isoformat() if row.last_ingested_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "chunk_count": row.chunk_count or 0,
        "token_count": row.token_count or 0,
        "uploaded_by": row.uploaded_by,
        "last_ingest_job_id": row.last_ingest_job_id,
        "metadata": row.metadata_json or {},
    }


def _serialize_knowledge_job(row) -> dict:
    return {
        "job_id": row.id,
        "status": row.status,
        "trigger_type": row.trigger_type,
        "created_by": row.created_by,
        "saved_files": row.saved_files or [],
        "result": row.result_json,
        "report": row.report_json,
        "error": row.error,
        "created_at": row.created_at.timestamp() if row.created_at else None,
        "updated_at": row.updated_at.timestamp() if row.updated_at else None,
        "started_at": row.started_at.timestamp() if row.started_at else None,
        "finished_at": row.finished_at.timestamp() if row.finished_at else None,
    }


def _sync_knowledge_documents_from_filesystem(db: Session, uploaded_by: str | None = None, job_id: str | None = None) -> None:
    docs_dir = _docs_root()
    docs_dir.mkdir(parents=True, exist_ok=True)
    state_lookup = _document_state_lookup()
    now = datetime.utcnow()
    seen_paths: set[str] = set()
    for path in docs_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_UPLOAD_EXTS:
            continue
        payload = _document_payload(path, docs_dir, state_lookup)
        metadata = payload.get("metadata") or {}
        relative_path = payload["relative_path"]
        seen_paths.add(relative_path)
        upsert_knowledge_document(
            db,
            relative_path=relative_path,
            filename=payload["filename"],
            file_type=payload["file_type"],
            file_size=payload["file_size"],
            sha256=metadata.get("sha256"),
            doc_id=metadata.get("doc_id"),
            indexed_status="indexed" if metadata else "pending",
            uploaded_by=uploaded_by,
            last_ingest_job_id=job_id,
            metadata_json=metadata,
            last_ingested_at=now if metadata else None,
        )

    rows, _ = list_knowledge_documents(db, status="active", limit=10000)
    for row in rows:
        if row.relative_path not in seen_paths:
            mark_knowledge_document_deleted(db, row.relative_path, job_id=job_id)


def _library_payload(r: redis.Redis | None = None, limit_jobs: int = 10, db: Session | None = None) -> dict:
    docs_dir = _docs_root()
    docs_dir.mkdir(parents=True, exist_ok=True)
    if db is not None:
        _sync_knowledge_documents_from_filesystem(db)
        rows, total = list_knowledge_documents(db, status="active", limit=10000)
        documents = [_serialize_knowledge_document(row) for row in rows]
        db_jobs = [_serialize_knowledge_job(row) for row in list_knowledge_jobs(db, limit_jobs)]
    else:
        state_lookup = _document_state_lookup()
        documents = [
            _document_payload(path, docs_dir, state_lookup)
            for path in sorted(docs_dir.rglob("*"), key=lambda item: item.stat().st_mtime, reverse=True)
            if path.is_file() and path.suffix.lower() in SUPPORTED_UPLOAD_EXTS
        ]
        total = len(documents)
        db_jobs = []
    latest_report = _load_latest_ingestion_report()
    jobs = db_jobs or (_list_ingest_jobs(r, limit_jobs) if r is not None else [])
    total_size = sum(int(doc["file_size"]) for doc in documents)
    return {
        "documents": documents,
        "jobs": jobs,
        "report": latest_report,
        "stats": {
            "total_documents": total,
            "indexed_documents": sum(1 for doc in documents if doc["status"] == "indexed"),
            "pending_documents": sum(1 for doc in documents if doc["status"] != "indexed"),
            "total_size": total_size,
            "supported_types": sorted(ext.lstrip(".") for ext in SUPPORTED_UPLOAD_EXTS),
        },
    }


def _run_ingest_job(job_id: str) -> None:
    r = getattr(get_app_state(), "redis", None)
    if r is None:
        return

    current = _load_ingest_job(r, job_id) or {}
    _save_ingest_job(r, job_id, {**current, "status": "running", "error": None})
    db = get_session_factory()()
    try:
        update_knowledge_job(db, job_id, status="running", error="")
    finally:
        db.close()

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
        db = get_session_factory()()
        try:
            update_knowledge_job(
                db,
                job_id,
                status="succeeded",
                result_json=result,
                report_json=report,
                error="",
            )
            _sync_knowledge_documents_from_filesystem(db, job_id=job_id)
        finally:
            db.close()
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
        db = get_session_factory()()
        try:
            update_knowledge_job(
                db,
                job_id,
                status="failed",
                result_json=None,
                report_json=report,
                error=str(e),
            )
        finally:
            db.close()


def _create_ingest_job(
    r: redis.Redis,
    saved_files: list[dict],
    background_tasks: BackgroundTasks,
    *,
    trigger_type: str,
    created_by: str | None,
    db: Session,
) -> str:
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
    create_knowledge_job(
        db,
        job_id=job_id,
        trigger_type=trigger_type,
        created_by=created_by,
        saved_files=saved_files,
    )
    background_tasks.add_task(_run_ingest_job, job_id)
    return job_id
