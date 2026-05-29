"""LlamaIndex Workflow implementation for the Agentic RAG control flow."""

from __future__ import annotations

import time
from typing import Callable, Optional

from llama_index.core.workflow import Context, Event, StartEvent, StopEvent, Workflow, step

from app.agentic.query_rewrite import RewriteResult, rewrite_query
from app.agentic.hyde import HyDEResult, generate_hyde_query
from app.agentic.step_back import StepBackResult, generate_step_back_question
from app.agentic.sub_question import DecompositionResult, decompose_question
from app.agentic.retrieval_quality import (
    QualityResult,
    QualityThresholds,
    check_retrieval_quality,
)
from app.agentic.router import IntentType, QueryStrategy, RouteResult, route_query
from app.agentic.rag_workflow import WorkflowResult, WorkflowTrace

RetrieveFn = Callable[[str, int], list]
RouteFn = Callable[[str], RouteResult]
RewriteFn = Callable[[str], RewriteResult]
QualityFn = Callable[[list, Optional[QualityThresholds]], QualityResult]
DecomposeFn = Callable[[str], DecompositionResult]
HyDEFn = Callable[[str], HyDEResult]
StepBackFn = Callable[[str], StepBackResult]


class WorkflowStepEvent(Event):
    """Event streamed to the API layer for frontend step rendering."""

    key: str
    title: str
    status: str
    summary: str = ""
    duration_ms: float | None = None
    step_items: list[dict] = []

    def to_payload(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "status": self.status,
            "summary": self.summary,
            "duration_ms": self.duration_ms,
            "items": self.step_items,
        }


class RoutedEvent(Event):
    query: str
    route: RouteResult
    route_payload: dict
    timings: dict


class RetrievedEvent(Event):
    original_query: str
    retrieval_query: str
    route: RouteResult
    route_payload: dict
    timings: dict
    nodes: list
    retry_count: int = 0
    rewrite_payload: dict = {}
    decomposition_payload: dict = {}
    hyde_payload: dict = {}
    step_back_payload: dict = {}
    sub_retrievals: list[dict] = []
    sub_question_results: list[dict] = []


class RewriteNeededEvent(Event):
    original_query: str
    retrieval_query: str
    route: RouteResult
    route_payload: dict
    timings: dict
    nodes: list
    quality: QualityResult
    decomposition_payload: dict = {}
    hyde_payload: dict = {}
    step_back_payload: dict = {}
    sub_retrievals: list[dict] = []
    sub_question_results: list[dict] = []


class AgenticRAGWorkflow(Workflow):
    """Router -> retrieval -> quality -> optional rewrite/retry workflow."""

    def __init__(
        self,
        *,
        retrieve_fn: RetrieveFn,
        route_fn: RouteFn = route_query,
        rewrite_fn: RewriteFn = rewrite_query,
        decompose_fn: DecomposeFn = decompose_question,
        hyde_fn: HyDEFn = generate_hyde_query,
        step_back_fn: StepBackFn = generate_step_back_question,
        quality_fn: QualityFn = check_retrieval_quality,
        quality_thresholds: Optional[QualityThresholds] = None,
        initial_top_k: int = 5,
        max_sub_questions: int = 4,
        max_retry: int = 1,
        timeout: int = 120,
        verbose: bool = False,
    ):
        super().__init__(timeout=timeout, verbose=verbose)
        self.retrieve_fn = retrieve_fn
        self.route_fn = route_fn
        self.rewrite_fn = rewrite_fn
        self.decompose_fn = decompose_fn
        self.hyde_fn = hyde_fn
        self.step_back_fn = step_back_fn
        self.quality_fn = quality_fn
        self.quality_thresholds = quality_thresholds
        self.initial_top_k = initial_top_k
        self.max_sub_questions = max_sub_questions
        self.max_retry = max_retry

    @step
    async def route_step(self, ctx: Context, ev: StartEvent) -> RoutedEvent:
        query = str(ev.query)
        started = time.perf_counter()
        route = self.route_fn(query)
        duration_ms = _elapsed_ms(started)
        route_payload = _route_to_dict(route)
        timings = {"router_ms": duration_ms}
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="router",
                title="1. Query Router",
                status="done",
                summary=f"路由完成：{route.intent.value}",
                duration_ms=duration_ms,
                step_items=[
                    {"label": "intent", "value": route.intent.value},
                    {"label": "method", "value": route.method},
                    {"label": "reason", "value": route.reason},
                ],
            )
        )
        return RoutedEvent(
            query=query,
            route=route,
            route_payload=route_payload,
            timings=timings,
        )

    @step
    async def retrieve_step(self, ctx: Context, ev: RoutedEvent) -> RetrievedEvent:
        if ev.route.query_strategy == QueryStrategy.DECOMPOSITION or ev.route.intent == IntentType.MULTI_STEP:
            return await self._multi_step_retrieve(ctx, ev)
        if ev.route.query_strategy == QueryStrategy.HYDE:
            return await self._hyde_retrieve(ctx, ev)
        if ev.route.query_strategy == QueryStrategy.STEP_BACK:
            return await self._step_back_retrieve(ctx, ev)

        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="retrieval",
                title="2. Retrieval",
                status="running",
                summary="正在执行混合检索与重排",
            )
        )
        started = time.perf_counter()
        nodes = self.retrieve_fn(ev.query, self.initial_top_k)
        duration_ms = _elapsed_ms(started)
        ev.timings["retrieval_ms"] = duration_ms
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="retrieval",
                title="2. Retrieval",
                status="done",
                summary=f"检索完成，召回 {len(nodes)} 个候选节点",
                duration_ms=duration_ms,
                step_items=[
                    {"label": "query", "value": ev.query},
                    {"label": "nodes", "value": str(len(nodes))},
                ],
            )
        )
        return RetrievedEvent(
            original_query=ev.query,
            retrieval_query=ev.query,
            route=ev.route,
            route_payload=ev.route_payload,
            timings=ev.timings,
            nodes=nodes,
        )

    async def _hyde_retrieve(self, ctx: Context, ev: RoutedEvent) -> RetrievedEvent:
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="hyde",
                title="2. HyDE Transform",
                status="running",
                summary="正在生成 hypothetical document 以增强语义召回",
            )
        )
        started = time.perf_counter()
        hyde = self.hyde_fn(ev.query)
        hyde_ms = _elapsed_ms(started)
        ev.timings["hyde_ms"] = hyde_ms
        hyde_payload = _hyde_to_dict(hyde)
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="hyde",
                title="2. HyDE Transform",
                status="done" if hyde.success else "warn",
                summary="HyDE 文档生成完成" if hyde.success else "HyDE 失败，回退原始问题检索",
                duration_ms=hyde_ms,
                step_items=[
                    {"label": "method", "value": hyde.method},
                    {"label": "query", "value": ev.query},
                    {"label": "document", "value": hyde.hypothetical_document[:220] or "-"},
                ],
            )
        )

        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="retrieval",
                title="3. Retrieval",
                status="running",
                summary="正在使用 HyDE 检索查询执行混合检索",
            )
        )
        retrieval_started = time.perf_counter()
        nodes = self.retrieve_fn(hyde.retrieval_query, self.initial_top_k)
        retrieval_ms = _elapsed_ms(retrieval_started)
        ev.timings["retrieval_ms"] = retrieval_ms
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="retrieval",
                title="3. Retrieval",
                status="done",
                summary=f"HyDE 检索完成，召回 {len(nodes)} 个候选节点",
                duration_ms=retrieval_ms,
                step_items=[
                    {"label": "query", "value": hyde.retrieval_query[:220]},
                    {"label": "nodes", "value": str(len(nodes))},
                ],
            )
        )
        return RetrievedEvent(
            original_query=ev.query,
            retrieval_query=hyde.retrieval_query,
            route=ev.route,
            route_payload=ev.route_payload,
            timings=ev.timings,
            nodes=nodes,
            hyde_payload=hyde_payload,
        )

    async def _step_back_retrieve(self, ctx: Context, ev: RoutedEvent) -> RetrievedEvent:
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="step_back",
                title="2. Step-back Transform",
                status="running",
                summary="正在生成上位背景问题",
            )
        )
        started = time.perf_counter()
        step_back = self.step_back_fn(ev.query)
        transform_ms = _elapsed_ms(started)
        ev.timings["step_back_ms"] = transform_ms
        step_back_payload = _step_back_to_dict(step_back)
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="step_back",
                title="2. Step-back Transform",
                status="done" if step_back.success else "warn",
                summary="Step-back 问题生成完成" if step_back.success else "Step-back 失败，回退原始问题",
                duration_ms=transform_ms,
                step_items=[
                    {"label": "method", "value": step_back.method},
                    {"label": "original", "value": ev.query},
                    {"label": "step_back", "value": step_back.step_back_question},
                ],
            )
        )

        retrieval_started = time.perf_counter()
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="retrieval",
                title="3. Step-back Retrieval",
                status="running",
                summary="正在补充具体证据与背景证据",
            )
        )
        original_started = time.perf_counter()
        original_nodes = self.retrieve_fn(ev.query, self.initial_top_k)
        original_ms = _elapsed_ms(original_started)
        background_started = time.perf_counter()
        background_nodes = self.retrieve_fn(step_back.step_back_question, self.initial_top_k)
        background_ms = _elapsed_ms(background_started)
        merged_nodes = _dedupe_nodes([*original_nodes, *background_nodes])
        retrieval_ms = _elapsed_ms(retrieval_started)
        ev.timings["retrieval_ms"] = retrieval_ms
        step_back_payload["retrievals"] = [
            {
                "kind": "original",
                "query": ev.query,
                "node_count": len(original_nodes),
                "duration_ms": original_ms,
            },
            {
                "kind": "step_back",
                "query": step_back.step_back_question,
                "node_count": len(background_nodes),
                "duration_ms": background_ms,
            },
        ]
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="retrieval",
                title="3. Step-back Retrieval",
                status="done",
                summary=f"双路检索完成，合并保留 {len(merged_nodes)} 个候选节点",
                duration_ms=retrieval_ms,
                step_items=[
                    {"label": "original_nodes", "value": str(len(original_nodes))},
                    {"label": "step_back_nodes", "value": str(len(background_nodes))},
                    {"label": "deduped_nodes", "value": str(len(merged_nodes))},
                ],
            )
        )
        return RetrievedEvent(
            original_query=ev.query,
            retrieval_query=f"{ev.query} | {step_back.step_back_question}",
            route=ev.route,
            route_payload=ev.route_payload,
            timings=ev.timings,
            nodes=merged_nodes,
            step_back_payload=step_back_payload,
        )

    async def _multi_step_retrieve(self, ctx: Context, ev: RoutedEvent) -> RetrievedEvent:
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="decomposition",
                title="2. Sub-question Decomposition",
                status="running",
                summary="Splitting complex question into retrieval-focused sub-questions",
            )
        )
        started = time.perf_counter()
        decomposition = self.decompose_fn(ev.query)
        sub_questions = (decomposition.sub_questions or [ev.query])[: self.max_sub_questions]
        decompose_ms = _elapsed_ms(started)
        ev.timings["decomposition_ms"] = decompose_ms
        decomposition_payload = _decomposition_to_dict(decomposition, sub_questions)
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="decomposition",
                title="2. Sub-question Decomposition",
                status="done",
                summary=f"Generated {len(sub_questions)} sub-question(s)",
                duration_ms=decompose_ms,
                step_items=[
                    {"label": f"q{idx}", "value": question}
                    for idx, question in enumerate(sub_questions, start=1)
                ],
            )
        )

        all_nodes: list = []
        sub_retrievals: list[dict] = []
        sub_question_results: list[dict] = []
        retrieval_started = time.perf_counter()
        for idx, question in enumerate(sub_questions, start=1):
            ctx.write_event_to_stream(
                WorkflowStepEvent(
                    key="retrieval",
                    title="3. Multi-hop Retrieval",
                    status="running",
                    summary=f"Retrieving evidence for sub-question {idx}/{len(sub_questions)}",
                    step_items=[{"label": "query", "value": question}],
                )
            )
            step_started = time.perf_counter()
            nodes = self.retrieve_fn(question, self.initial_top_k)
            step_ms = _elapsed_ms(step_started)
            all_nodes.extend(nodes)
            sub_question_results.append(
                {
                    "index": idx,
                    "query": question,
                    "nodes": nodes,
                }
            )
            sub_retrievals.append(
                {
                    "index": idx,
                    "query": question,
                    "node_count": len(nodes),
                    "duration_ms": step_ms,
                    "top_score": _node_score(nodes[0]) if nodes else None,
                }
            )

        deduped_nodes = _dedupe_nodes(all_nodes)
        retrieval_ms = _elapsed_ms(retrieval_started)
        ev.timings["retrieval_ms"] = retrieval_ms
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="retrieval",
                title="3. Multi-hop Retrieval",
                status="done",
                summary=f"Retrieved {len(all_nodes)} nodes and kept {len(deduped_nodes)} after dedupe",
                duration_ms=retrieval_ms,
                step_items=[
                    {"label": "sub_questions", "value": str(len(sub_questions))},
                    {"label": "raw_nodes", "value": str(len(all_nodes))},
                    {"label": "deduped_nodes", "value": str(len(deduped_nodes))},
                ],
            )
        )
        return RetrievedEvent(
            original_query=ev.query,
            retrieval_query=" | ".join(sub_questions),
            route=ev.route,
            route_payload=ev.route_payload,
            timings=ev.timings,
            nodes=deduped_nodes,
            decomposition_payload=decomposition_payload,
            sub_retrievals=sub_retrievals,
            sub_question_results=sub_question_results,
        )

    @step
    async def quality_step(
        self, ctx: Context, ev: RetrievedEvent
    ) -> StopEvent | RewriteNeededEvent:
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="quality",
                title="3. Quality Check",
                status="running",
                summary="正在判断召回质量",
            )
        )
        started = time.perf_counter()
        quality = self.quality_fn(ev.nodes, self.quality_thresholds)
        duration_ms = _elapsed_ms(started)
        timing_key = "retry_quality_ms" if ev.retry_count else "quality_ms"
        ev.timings[timing_key] = duration_ms
        status = "warn" if quality.quality == "bad" else "done"
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="quality",
                title="3. Quality Check",
                status=status,
                summary=quality.reason,
                duration_ms=duration_ms,
                step_items=[
                    {"label": "quality", "value": quality.quality},
                    {"label": "top1", "value": str(round(quality.top1_score or 0.0, 4))},
                    {"label": "nodes", "value": str(quality.node_count)},
                    {"label": "retry?", "value": str(bool(quality.should_retry))},
                ],
            )
        )
        if (
            quality.quality == "bad"
            and quality.should_retry
            and ev.retry_count < self.max_retry
        ):
            return RewriteNeededEvent(
                original_query=ev.original_query,
                retrieval_query=ev.retrieval_query,
                route=ev.route,
                route_payload=ev.route_payload,
                timings=ev.timings,
                nodes=ev.nodes,
                quality=quality,
                decomposition_payload=ev.decomposition_payload,
                hyde_payload=ev.hyde_payload,
                step_back_payload=ev.step_back_payload,
                sub_retrievals=ev.sub_retrievals,
                sub_question_results=ev.sub_question_results,
            )

        if not ev.rewrite_payload:
            ctx.write_event_to_stream(
                WorkflowStepEvent(
                    key="rewrite",
                    title="4. Query Rewrite",
                    status="skipped",
                    summary="召回质量可接受，未触发 Query Rewrite",
                    duration_ms=0,
                )
            )
        return StopEvent(result=_build_result(ev, quality))

    @step
    async def rewrite_step(self, ctx: Context, ev: RewriteNeededEvent) -> RetrievedEvent:
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="rewrite",
                title="4. Query Rewrite",
                status="running",
                summary="检索质量不足，正在改写问题并重试",
            )
        )
        started = time.perf_counter()
        rewrite = self.rewrite_fn(ev.original_query)
        duration_ms = _elapsed_ms(started)
        ev.timings["rewrite_ms"] = duration_ms
        retrieval_query = rewrite.rewritten or ev.original_query
        rewrite_payload = _rewrite_to_dict(rewrite)
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="rewrite",
                title="4. Query Rewrite",
                status="done",
                summary="问题改写完成",
                duration_ms=duration_ms,
                step_items=[
                    {"label": "method", "value": rewrite.method},
                    {"label": "rewritten", "value": retrieval_query},
                ],
            )
        )

        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="retrieval",
                title="2. Retrieval",
                status="running",
                summary="正在使用改写后的问题重新检索",
            )
        )
        retry_top_k = ev.quality.suggested_top_k or self.initial_top_k
        started = time.perf_counter()
        nodes = self.retrieve_fn(retrieval_query, retry_top_k)
        duration_ms = _elapsed_ms(started)
        ev.timings["retry_retrieval_ms"] = duration_ms
        ctx.write_event_to_stream(
            WorkflowStepEvent(
                key="retrieval",
                title="2. Retrieval",
                status="done",
                summary=f"重试检索完成，召回 {len(nodes)} 个候选节点",
                duration_ms=duration_ms,
                step_items=[
                    {"label": "query", "value": retrieval_query},
                    {"label": "nodes", "value": str(len(nodes))},
                    {"label": "retry", "value": "1"},
                ],
            )
        )
        return RetrievedEvent(
            original_query=ev.original_query,
            retrieval_query=retrieval_query,
            route=ev.route,
            route_payload=ev.route_payload,
            timings=ev.timings,
            nodes=nodes,
            retry_count=1,
            rewrite_payload=rewrite_payload,
            decomposition_payload=ev.decomposition_payload,
            hyde_payload=ev.hyde_payload,
            step_back_payload=ev.step_back_payload,
            sub_retrievals=ev.sub_retrievals,
            sub_question_results=ev.sub_question_results,
        )


def _build_result(ev: RetrievedEvent, quality: QualityResult) -> WorkflowResult:
    trace = WorkflowTrace(
        route=ev.route_payload,
        original_query=ev.original_query,
        retrieval_query=ev.retrieval_query,
        decomposition={
            **ev.decomposition_payload,
            "sub_retrievals": ev.sub_retrievals,
        }
        if ev.decomposition_payload or ev.sub_retrievals
        else {},
        hyde=ev.hyde_payload,
        step_back=ev.step_back_payload,
        rewrite=ev.rewrite_payload,
        quality=_quality_to_dict(quality),
        retry_count=ev.retry_count,
        timings=ev.timings,
    )
    return WorkflowResult(
        answer="",
        intent=ev.route.intent.value,
        route_method=ev.route.method,
        quality=quality.quality,
        retry_count=ev.retry_count,
        rewrite_used=bool(ev.rewrite_payload),
        original_query=ev.original_query,
        rewritten_query=ev.retrieval_query if ev.rewrite_payload else "",
        retrieval_query=ev.retrieval_query,
        source_nodes=ev.nodes,
        sub_question_results=ev.sub_question_results,
        top1_score=quality.top1_score or 0.0,
        total_text_length=quality.total_text_length,
        route=ev.route,
        quality_result=quality,
        trace=trace,
    )


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def _route_to_dict(route: RouteResult) -> dict:
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


def _rewrite_to_dict(rewrite: RewriteResult) -> dict:
    return {
        "original": rewrite.original,
        "rewritten": rewrite.rewritten,
        "keywords": rewrite.keywords,
        "method": rewrite.method,
        "success": rewrite.success,
    }


def _decomposition_to_dict(
    decomposition: DecompositionResult,
    sub_questions: list[str],
) -> dict:
    return {
        "original": decomposition.original,
        "sub_questions": sub_questions,
        "reason": decomposition.reason,
        "method": decomposition.method,
        "success": decomposition.success,
    }


def _hyde_to_dict(hyde: HyDEResult) -> dict:
    return {
        "original_query": hyde.original_query,
        "hypothetical_document": hyde.hypothetical_document,
        "retrieval_query": hyde.retrieval_query,
        "method": hyde.method,
        "reason": hyde.reason,
        "success": hyde.success,
        "error": hyde.error,
    }


def _step_back_to_dict(step_back: StepBackResult) -> dict:
    return {
        "original_query": step_back.original_query,
        "step_back_question": step_back.step_back_question,
        "method": step_back.method,
        "reason": step_back.reason,
        "success": step_back.success,
        "error": step_back.error,
    }


def _quality_to_dict(quality: QualityResult) -> dict:
    return {
        "quality": quality.quality,
        "reason": quality.reason,
        "top1_score": quality.top1_score,
        "node_count": quality.node_count,
        "total_text_length": quality.total_text_length,
        "source_count": quality.source_count,
        "auto_merged": quality.auto_merged,
        "should_retry": quality.should_retry,
        "suggested_top_k": quality.suggested_top_k,
    }


def _dedupe_nodes(nodes: list) -> list:
    deduped: list = []
    seen: set[str] = set()
    for node in nodes:
        key = _node_key(node)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(node)
    return deduped


def _node_key(node: object) -> str:
    metadata = getattr(node, "metadata", None)
    inner_node = getattr(node, "node", None)
    if metadata is None and inner_node is not None:
        metadata = getattr(inner_node, "metadata", None)
    if isinstance(metadata, dict):
        for field_name in ("chunk_id", "paragraph_id", "doc_id", "file_path"):
            value = metadata.get(field_name)
            if value:
                return f"{field_name}:{value}"

    node_id = getattr(node, "node_id", None)
    if node_id:
        return f"node_id:{node_id}"
    if inner_node is not None:
        inner_node_id = getattr(inner_node, "node_id", None)
        if inner_node_id:
            return f"node_id:{inner_node_id}"
    return f"text:{str(node)[:512]}"


def _node_score(node: object) -> float | None:
    score = getattr(node, "score", None)
    if score is None:
        return None
    try:
        return round(float(score), 4)
    except (TypeError, ValueError):
        return None

