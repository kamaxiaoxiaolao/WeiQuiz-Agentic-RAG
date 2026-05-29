from dataclasses import dataclass

from app.agentic.node_synthesizer import (
    build_citations_from_nodes,
    build_prompt_from_nodes,
    collect_context_snippets,
    format_source_label,
)


@dataclass
class MockNode:
    text: str
    score: float
    metadata: dict


def test_collect_context_snippets_sorts_deduplicates_and_truncates():
    nodes = [
        MockNode("low score text", 0.1, {"source_path": "low.txt"}),
        MockNode("high score text", 0.9, {"source_path": "high.txt"}),
        MockNode("high score text", 0.8, {"source_path": "dup.txt"}),
    ]

    snippets = collect_context_snippets(nodes, max_context_chars=30)

    assert len(snippets) == 2
    assert snippets[0].text == "high score text"
    assert snippets[0].source.source_path == "high.txt"
    assert sum(len(item.text) for item in snippets) <= 30


def test_build_prompt_from_nodes_contains_context_and_question():
    nodes = [
        MockNode(
            "Quantum Gateway replaced Nginx with Envoy for xDS and observability.",
            0.9,
            {
                "file_name": "gateway.txt",
                "source_path": "data/docs/gateway.txt",
                "chunk_id": "chunk-1",
            },
        )
    ]

    prompt = build_prompt_from_nodes("为什么替换为 Envoy？", nodes)

    assert "知识库上下文" in prompt
    assert "为什么替换为 Envoy？" in prompt
    assert "Quantum Gateway" in prompt
    assert "gateway.txt" in prompt


def test_format_source_label_uses_metadata():
    node = MockNode(
        "text",
        0.9,
        {"file_name": "a.pdf", "page_range": "1-2", "chunk_id": "c1"},
    )
    snippet = collect_context_snippets([node])[0]

    label = format_source_label(snippet.source)

    assert "a.pdf" in label
    assert "1-2" in label
    assert "c1" in label


def test_build_citations_from_nodes_returns_stable_payload():
    nodes = [
        MockNode(
            "text",
            0.9,
            {
                "doc_id": "doc-a",
                "file_name": "a.pdf",
                "source_path": "data/docs/a.pdf",
                "chunk_id": "c1",
                "page_range": "1-2",
            },
        )
    ]

    citations = build_citations_from_nodes(nodes)

    assert citations[0]["source_id"] == 1
    assert citations[0]["doc_id"] == "doc-a"
    assert citations[0]["file_name"] == "a.pdf"
    assert citations[0]["chunk_id"] == "c1"
