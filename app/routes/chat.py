"""Streaming chat route."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import time

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from llama_index.core.schema import QueryBundle
from sqlalchemy.orm import Session

from app.agentic.controller import AgentController, AgentMode
from app.agentic.grounding import check_answer_grounding, should_run_grounding
from app.agentic.llama_workflow import AgenticRAGWorkflow, WorkflowStepEvent
from app.agentic.node_synthesizer import (
    build_citations_from_nodes,
    build_fallback_answer_from_intermediate,
    stream_answer_from_nodes,
    synthesize_intermediate_answers,
)
from app.api_support.helpers import (
    WORKFLOW_RETRIEVAL_TIMEOUT_SECONDS,
    _controller_decision_payload,
    _elapsed_ms,
    _handle_tool_call_decision,
    _is_rerank_postprocessor,
    _long_term_memory_service,
    _memory_service,
    _merge_trace_timings,
    _retrieval_timing_key,
    _route_only_trace,
    _route_payload,
    _schedule_long_term_memory_add,
    _schedule_memory_compression,
    _should_use_simple_rag_fast_path,
    _source_node_payload,
    _sse_event,
    _step_payload,
    _stream_lightweight_chat,
    _summarize_retrieval_profiles,
    _tool_registry,
)
from app.api_support.schemas import ChatRequest
from app.api_support.state import get_app_state
from app.auth.dependencies import get_current_user
from app.auth.repository import create_chat_session, get_chat_session, get_db, update_session_last_message
from app.config import settings as app_settings
from app.observability import add_span_event, set_span_attributes, start_span
from app.retrieval.cache import RetrievalCache
from app.storage.auth_models import User


router = APIRouter()


@router.post("/chat/stream")
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
    with start_span(
        "agent.controller",
        user_id=current_user.id,
        session_id=request.session_id,
        query=request.message,
    ) as controller_span:
        decision = AgentController(_tool_registry()).decide(request.message)
        set_span_attributes(
            controller_span,
            {
                "agent.mode": decision.mode.value,
                "agent.intent": decision.route.intent.value,
                "agent.strategy": decision.route.query_strategy.value,
                "agent.route_method": decision.route.method,
                "agent.need_grounding": decision.need_grounding,
                "agent.max_retries": decision.max_retries,
            },
        )
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
    with start_span(
        "memory.load_context",
        user_id=current_user.id,
        session_id=request.session_id,
        use_recent_messages=decision.memory_policy.use_recent_messages,
        use_session_summary=decision.memory_policy.use_session_summary,
        use_long_term_memory=decision.memory_policy.use_long_term_memory,
    ) as memory_span:
        memory = memory_service.load(request.session_id)
        memory_context = memory_service.build_context(
            request.session_id,
            memory,
            db=db,
            use_recent_messages=decision.memory_policy.use_recent_messages,
            use_session_summary=decision.memory_policy.use_session_summary,
        )
        long_term_memory_service = _long_term_memory_service()
        if decision.memory_policy.use_long_term_memory:
            memory_context.long_term_memories = long_term_memory_service.search(
                user_id=current_user.id,
                query=request.message,
                limit=decision.memory_policy.long_term_top_k,
            )
        set_span_attributes(
            memory_span,
            {
                "memory.used_summary": memory_context.used_summary,
                "memory.has_context": memory_context.has_context,
                "memory.recent_message_count": len(memory_context.recent_messages),
                "memory.long_term_count": len(memory_context.long_term_memories),
            },
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
        answer, trace = await _handle_tool_call_decision(decision, request.message, current_user, route_ms)
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

    index = getattr(get_app_state(), "index", None)
    retriever = getattr(get_app_state(), "retriever", None)
    reranker = getattr(get_app_state(), "reranker", None)

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
        query_engine = getattr(get_app_state(), "query_engine", None)
        base_retriever = getattr(get_app_state(), "retriever", None)
        retrieval_cache = RetrievalCache(getattr(get_app_state(), "redis", None))
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
            with start_span(
                "rag.retrieval",
                query=query,
                top_k=top_k,
                vector_backend=app_settings.vector_store_backend,
                rerank_enabled=app_settings.rerank_enabled,
                auto_merging_enabled=app_settings.auto_merging_enabled,
            ) as retrieval_span:
                cached_nodes, cache_metadata = retrieval_cache.get(query, top_k=top_k)
                set_span_attributes(
                    retrieval_span,
                    {
                        "retrieval.cache_enabled": cache_metadata.get("enabled"),
                        "retrieval.cache_hit": cache_metadata.get("hit"),
                        "retrieval.cache_key": cache_metadata.get("key"),
                        "retrieval.kb_version": cache_metadata.get("kb_version"),
                    },
                )
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
                    set_span_attributes(
                        retrieval_span,
                        {
                            "retrieval.final_node_count": len(cached_nodes),
                            "retrieval.total_ms": cache_metadata.get("read_ms", 0),
                            "retrieval.fallback": False,
                        },
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
                    set_span_attributes(
                        retrieval_span,
                        {
                            "retrieval.fallback": False,
                            "retrieval.retriever_core_ms": profile.get("retriever_core_ms"),
                            "retrieval.final_node_count": profile.get("final_node_count"),
                            "retrieval.total_ms": profile.get("retrieval_total_profiled_ms"),
                            "retrieval.postprocessor_count": len(profile.get("postprocessors") or []),
                        },
                    )
                    for item in profile.get("postprocessors") or []:
                        add_span_event(
                            retrieval_span,
                            "postprocessor",
                            {
                                "name": item.get("name"),
                                "status": item.get("status"),
                                "duration_ms": item.get("duration_ms"),
                                "node_count": item.get("node_count"),
                            },
                        )
                    return nodes
                except concurrent.futures.TimeoutError:
                    future.cancel()
                    set_span_attributes(retrieval_span, {"retrieval.timeout": True})
                    print(f"[Retrieval] profiled retrieve timeout after {WORKFLOW_RETRIEVAL_TIMEOUT_SECONDS}s, fallback to base retriever: {query}")
                except Exception as exc:
                    set_span_attributes(retrieval_span, {"retrieval.error": str(exc)})
                    print(f"[Retrieval] profiled retrieve failed, fallback to base retriever: {exc}")
                finally:
                    executor.shutdown(wait=False, cancel_futures=True)

                if base_retriever is None:
                    set_span_attributes(retrieval_span, {"retrieval.fallback": True, "retrieval.final_node_count": 0})
                    return []
                try:
                    fallback_start = time.perf_counter()
                    nodes = base_retriever.retrieve(QueryBundle(query))
                    fallback_ms = _elapsed_ms(fallback_start)
                    retrieval_profiles.append(
                        {
                            "query": query,
                            "fallback": True,
                            "fallback_reasons": ["profiled_retrieve_timeout_or_error"],
                            "retriever_core_ms": fallback_ms,
                            "retriever_node_count": len(nodes),
                            "final_node_count": len(nodes),
                            "postprocessors": [],
                            "retrieval_total_profiled_ms": fallback_ms,
                        }
                    )
                    set_span_attributes(
                        retrieval_span,
                        {
                            "retrieval.fallback": True,
                            "retrieval.final_node_count": len(nodes),
                            "retrieval.total_ms": fallback_ms,
                        },
                    )
                    return nodes
                except Exception as exc:
                    set_span_attributes(retrieval_span, {"retrieval.fallback_error": str(exc)})
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
                with start_span(
                    "rag.generation",
                    mode="simple_rag_fast_path",
                    query=request.message,
                    node_count=len(source_node_objects),
                    citation_count=len(citations),
                ) as generation_span:
                    for token in stream_answer_from_nodes(
                        request.message,
                        source_node_objects,
                        memory_context=memory_context,
                        intermediate_answers=[],
                    ):
                        answer_parts.append(token)
                        yield f"event: chunk\ndata: {token.replace(chr(10), '\\n')}\n\n"
                    set_span_attributes(
                        generation_span,
                        {
                            "generation.output_chars": len("".join(answer_parts)),
                            "generation.token_chunks": len(answer_parts),
                        },
                    )
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
        workflow_span_cm = start_span(
            "rag.workflow",
            query=request.message,
            strategy=decision.rag_strategy.value,
            max_retry=decision.max_retries,
            initial_top_k=5,
        )
        workflow_span = workflow_span_cm.__enter__()
        handler = workflow.run(query=request.message)

        yield f"event: route\ndata: {route_json}\n\n"
        yield _sse_event("status", "正在执行 LlamaIndex Workflow...")

        try:
            async for event in handler.stream_events():
                if isinstance(event, WorkflowStepEvent):
                    add_span_event(
                        workflow_span,
                        "workflow_step",
                        event.to_payload(),
                    )
                    yield _sse_event("step", event.to_payload())

            workflow_result = await handler
            set_span_attributes(
                workflow_span,
                {
                    "workflow.source_node_count": len(workflow_result.source_nodes),
                    "workflow.retrieval_query": workflow_result.retrieval_query or request.message,
                    "workflow.sub_question_count": len(workflow_result.sub_question_results or []),
                },
            )
        except asyncio.CancelledError:
            set_span_attributes(workflow_span, {"workflow.cancelled": True})
            workflow_span_cm.__exit__(None, None, None)
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
            set_span_attributes(workflow_span, {"workflow.error": str(exc)})
            workflow_span_cm.__exit__(None, None, None)
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
        finally:
            if "workflow_result" in locals():
                workflow_span_cm.__exit__(None, None, None)

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
            with start_span(
                "rag.generation",
                mode="agentic_workflow",
                query=retrieval_query,
                original_query=request.message,
                node_count=len(workflow_result.source_nodes),
                intermediate_answer_count=len(intermediate_answers),
            ) as generation_span:
                for token in stream_answer_from_nodes(
                    retrieval_query,
                    workflow_result.source_nodes,
                    memory_context=memory_context,
                    intermediate_answers=intermediate_answers,
                ):
                    answer_parts.append(token)
                    safe_token = token.replace("\n", "\\n")
                    yield f"event: chunk\ndata: {safe_token}\n\n"
                set_span_attributes(
                    generation_span,
                    {
                        "generation.output_chars": len("".join(answer_parts)),
                        "generation.token_chunks": len(answer_parts),
                    },
                )
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
            with start_span(
                "rag.grounding",
                mode=grounding_mode,
                source=grounding_source,
                question=request.message,
                node_count=len(workflow_result.source_nodes),
                answer_chars=len(final_answer),
            ) as grounding_span:
                grounding_result = check_answer_grounding(
                    question=request.message,
                    answer=final_answer,
                    nodes=workflow_result.source_nodes,
                )
                set_span_attributes(
                    grounding_span,
                    {
                        "grounding.verdict": grounding_result.verdict,
                        "grounding.score": grounding_result.grounding_score,
                        "grounding.unsupported_count": len(grounding_result.unsupported_points),
                        "grounding.summary": grounding_result.summary,
                    },
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
