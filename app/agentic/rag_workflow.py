"""Agentic RAG workflow orchestration.

This module is intentionally a thin orchestration layer. It does not own the
FastAPI session memory or the final LlamaIndex chat engine. Its job is to
coordinate router, optional query rewrite, retrieval, quality check, and trace
data so the API layer can decide how to generate the final answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.agentic.query_rewrite import RewriteResult, rewrite_query
from app.agentic.retrieval_quality import (
    QualityResult,
    QualityThresholds,
    check_retrieval_quality,
)
from app.agentic.router import IntentType, RouteResult, route_query
from app.metadata_schema import SourceNodePayload

logger = logging.getLogger(__name__)

RetrieveFn = Callable[[str, int], list]
AnswerFn = Callable[[str, list], str]
RouteFn = Callable[[str], RouteResult]
RewriteFn = Callable[[str], RewriteResult]
QualityFn = Callable[[list, Optional[QualityThresholds]], QualityResult]

_retrieve_fn: Optional[RetrieveFn] = None


@dataclass
class WorkflowTrace:
    """Trace payload returned to the frontend for observability."""

    route: dict
    original_query: str
    retrieval_query: str = ""
    decomposition: dict = field(default_factory=dict)
    hyde: dict = field(default_factory=dict)
    step_back: dict = field(default_factory=dict)
    rewrite: dict = field(default_factory=dict)
    quality: dict = field(default_factory=dict)
    retry_count: int = 0
    timings: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "route": self.route,
            "original_query": self.original_query,
            "retrieval_query": self.retrieval_query,
            "decomposition": self.decomposition,
            "hyde": self.hyde,
            "step_back": self.step_back,
            "rewrite": self.rewrite,
            "quality": self.quality,
            "retry_count": self.retry_count,
            "timings": self.timings,
        }


@dataclass
class WorkflowResult:
    """Result of the Agentic RAG workflow."""

    answer: str
    intent: str
    route_method: str
    quality: str
    retry_count: int = 0
    rewrite_used: bool = False
    original_query: str = ""
    rewritten_query: str = ""
    retrieval_query: str = ""
    source_nodes: list = field(default_factory=list)
    sub_question_results: list = field(default_factory=list)
    top1_score: float = 0.0
    total_text_length: int = 0
    route: Optional[RouteResult] = None
    quality_result: Optional[QualityResult] = None
    trace: WorkflowTrace | None = None

    def to_trace_dict(self) -> dict:
        if self.trace is None:
            return {}
        return self.trace.to_dict()


def set_retrieve_fn(fn: Optional[RetrieveFn]) -> None:
    """Set the retrieval function used by run_agentic_rag.

    Kept for tests and CLI demos. The FastAPI layer can pass retrieve_fn
    directly instead of relying on this global.
    """

    global _retrieve_fn
    _retrieve_fn = fn


def generate_answer(query: str, source_nodes: list) -> str:
    """Generate a lightweight fallback answer from retrieved context.

    The production /chat path should still use LlamaIndex's chat engine or a
    dedicated LLM answer function. This helper avoids network calls in workflow
    tests and gives a safe fallback for CLI debugging.
    """

    if not source_nodes:
        return "未检索到足够可靠的知识库内容，暂时不能基于资料回答。"

    previews = []
    for idx, node in enumerate(source_nodes[:3], start=1):
        text = _node_text(node).strip().replace("\n", " ")
        source = SourceNodePayload.from_node(node).source_path
        prefix = f"资料{idx}"
        if source:
            prefix += f"（{source}）"
        previews.append(f"{prefix}: {text[:180]}")

    return f"已根据知识库检索到的资料整理回答。检索问题：{query}\n\n" + "\n".join(previews)


def run_agentic_rag(
    query: str,
    *,
    retrieve_fn: Optional[RetrieveFn] = None,
    answer_fn: Optional[AnswerFn] = None,
    route_fn: RouteFn = route_query,
    rewrite_fn: RewriteFn = rewrite_query,
    quality_fn: QualityFn = check_retrieval_quality,
    quality_thresholds: Optional[QualityThresholds] = None,
    max_retry: int = 1,
    initial_top_k: int = 5,
) -> WorkflowResult:
    """Run router -> retrieval -> quality -> optional rewrite retry.

    The function returns source nodes and trace metadata. Answer generation is
    optional so the API layer can keep using the existing chat engine.
    """

    route_result = route_fn(query)
    trace = WorkflowTrace(
        route=_route_to_dict(route_result),
        original_query=query,
        retrieval_query=query,
    )

    if route_result.intent == IntentType.CHITCHAT:
        answer = answer_fn(query, []) if answer_fn else "你好，我是 WeiQuiz Enterprise RAG 助手。"
        return WorkflowResult(
            answer=answer,
            intent=route_result.intent.value,
            route_method=route_result.method,
            quality="chitchat",
            original_query=query,
            retrieval_query=query,
            route=route_result,
            trace=trace,
        )

    retriever = retrieve_fn or _retrieve_fn
    if retriever is None:
        quality_result = QualityResult(
            quality="bad",
            reason="retriever is not configured",
            should_retry=False,
        )
        trace.quality = _quality_to_dict(quality_result)
        return WorkflowResult(
            answer="检索器尚未配置，无法执行知识库检索。",
            intent=route_result.intent.value,
            route_method=route_result.method,
            quality=quality_result.quality,
            original_query=query,
            retrieval_query=query,
            route=route_result,
            quality_result=quality_result,
            trace=trace,
        )

    retrieval_query = query
    nodes = retriever(retrieval_query, initial_top_k)
    quality_result = quality_fn(nodes, quality_thresholds)

    retry_count = 0
    rewrite_used = False
    rewrite_payload: dict = {}

    if (
        quality_result.quality == "bad"
        and quality_result.should_retry
        and max_retry > 0
    ):
        rewrite_result = rewrite_fn(query)
        retrieval_query = rewrite_result.rewritten or query
        rewrite_used = True
        retry_count = 1
        rewrite_payload = _rewrite_to_dict(rewrite_result)

        retry_top_k = quality_result.suggested_top_k or initial_top_k
        nodes = retriever(retrieval_query, retry_top_k)
        quality_result = quality_fn(nodes, quality_thresholds)

    answer = ""
    if answer_fn is not None:
        answer = answer_fn(retrieval_query, nodes)
    else:
        answer = generate_answer(retrieval_query, nodes)

    trace.retrieval_query = retrieval_query
    trace.rewrite = rewrite_payload
    trace.quality = _quality_to_dict(quality_result)
    trace.retry_count = retry_count

    return WorkflowResult(
        answer=answer,
        intent=route_result.intent.value,
        route_method=route_result.method,
        quality=quality_result.quality,
        retry_count=retry_count,
        rewrite_used=rewrite_used,
        original_query=query,
        rewritten_query=retrieval_query if rewrite_used else "",
        retrieval_query=retrieval_query,
        source_nodes=nodes,
        top1_score=quality_result.top1_score or 0.0,
        total_text_length=quality_result.total_text_length,
        route=route_result,
        quality_result=quality_result,
        trace=trace,
    )


def _node_text(node: object) -> str:
    if hasattr(node, "text"):
        return str(getattr(node, "text") or "")
    inner_node = getattr(node, "node", None)
    if inner_node is not None and hasattr(inner_node, "text"):
        return str(getattr(inner_node, "text") or "")
    return str(node or "")


def _route_to_dict(route: RouteResult) -> dict:
    return {
        "intent": route.intent.value,
        "method": route.method,
        "reason": route.reason,
        "confidence": route.confidence,
    }


def _rewrite_to_dict(rewrite: RewriteResult) -> dict:
    return {
        "original": rewrite.original,
        "rewritten": rewrite.rewritten,
        "keywords": rewrite.keywords,
        "method": rewrite.method,
        "success": rewrite.success,
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
