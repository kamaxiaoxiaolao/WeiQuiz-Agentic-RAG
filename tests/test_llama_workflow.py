import pytest

from app.agentic.llama_workflow import AgenticRAGWorkflow, WorkflowStepEvent
from app.agentic.retrieval_quality import QualityResult
from app.agentic.router import IntentType, RouteResult


class MockNode:
    text = "mock context " * 80
    score = 0.8
    metadata = {"source_path": "mock.txt"}


def make_route() -> RouteResult:
    return RouteResult(
        intent=IntentType.KNOWLEDGE_BASE,
        method="test",
        reason="unit test",
        confidence=1.0,
    )


@pytest.mark.asyncio
async def test_llama_workflow_streams_steps_and_returns_result():
    workflow = AgenticRAGWorkflow(
        retrieve_fn=lambda query, top_k: [MockNode()],
        route_fn=lambda query: make_route(),
        quality_fn=lambda nodes, thresholds: QualityResult(
            quality="good",
            reason="enough context",
            should_retry=False,
            top1_score=0.8,
            node_count=len(nodes),
            total_text_length=500,
        ),
    )

    handler = workflow.run(query="test query")
    streamed_steps = []
    async for event in handler.stream_events():
        if isinstance(event, WorkflowStepEvent):
            streamed_steps.append(event.key)

    result = await handler

    assert result.quality == "good"
    assert result.retrieval_query == "test query"
    assert result.source_nodes
    assert {"router", "retrieval", "quality", "rewrite"}.issubset(set(streamed_steps))
    assert result.trace is not None
    assert result.trace.timings["retrieval_ms"] >= 0


@pytest.mark.asyncio
async def test_llama_workflow_rewrites_and_retries_when_quality_is_bad():
    calls = []

    def retrieve(query: str, top_k: int):
        calls.append((query, top_k))
        return [] if len(calls) == 1 else [MockNode()]

    workflow = AgenticRAGWorkflow(
        retrieve_fn=retrieve,
        route_fn=lambda query: make_route(),
        rewrite_fn=lambda query: type(
            "Rewrite",
            (),
            {
                "original": query,
                "rewritten": "rewritten query",
                "keywords": ["rewritten"],
                "method": "test",
                "success": True,
            },
        )(),
        quality_fn=lambda nodes, thresholds: QualityResult(
            quality="bad" if not nodes else "good",
            reason="empty" if not nodes else "ok",
            should_retry=not bool(nodes),
            suggested_top_k=8,
            node_count=len(nodes),
            total_text_length=0 if not nodes else 500,
        ),
    )

    result = await workflow.run(query="original query")

    assert result.retry_count == 1
    assert result.rewrite_used is True
    assert result.retrieval_query == "rewritten query"
    assert calls == [("original query", 5), ("rewritten query", 8)]
