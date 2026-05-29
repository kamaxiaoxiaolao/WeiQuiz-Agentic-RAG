"""Generate grounded answers from already-retrieved RAG nodes.

The API layer runs retrieval and Agentic workflow first. This module only
performs evidence synthesis, so it will not trigger another retrieval pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Sequence

from openai import OpenAI

from app.config import settings
from app.metadata_schema import SourceNodePayload
from app.services.memory_service import MemoryContext


DEFAULT_MAX_CONTEXT_CHARS = 8000
LLM_REQUEST_TIMEOUT_SECONDS = 45
DEFAULT_MAX_HISTORY_TURNS = 4


@dataclass
class ContextSnippet:
    index: int
    text: str
    source: SourceNodePayload


def build_citations_from_nodes(nodes: Sequence, max_citations: int = 5) -> list[dict]:
    """Build stable citation payloads from final workflow nodes."""

    citations = []
    for idx, node in enumerate(sort_nodes_by_score(nodes)[:max_citations], start=1):
        source = SourceNodePayload.from_node(node)
        citations.append(
            {
                "source_id": idx,
                "doc_id": source.doc_id,
                "file_name": source.file_name,
                "source_path": source.parent_source_path or source.source_path,
                "chunk_id": source.child_chunk_id or source.chunk_id,
                "parent_id": source.parent_id,
                "page_range": source.parent_page_range,
                "section_title": source.parent_section_title,
                "score": source.score,
                "retrieval_mode": source.retrieval_mode,
            }
        )
    return citations


def stream_answer_from_nodes(
    query: str,
    nodes: Sequence,
    *,
    memory_context: MemoryContext | None = None,
    intermediate_answers: Sequence[dict] | None = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> Iterator[str]:
    """Stream a final answer from final workflow nodes and optional sub-answers."""

    prompt = build_prompt_from_nodes(
        query=query,
        nodes=nodes,
        memory_context=memory_context,
        intermediate_answers=intermediate_answers,
        max_context_chars=max_context_chars,
    )
    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_api_base,
        timeout=LLM_REQUEST_TIMEOUT_SECONDS,
    )
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是 WeiQuiz Enterprise RAG 的企业知识库问答助手。"
                    "你只能基于给定的知识库上下文和中间答案回答。"
                    "如果证据不足，要明确说明缺少哪些信息。"
                    "回答要结构清晰，并尽量引用来源编号。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        stream=True,
    )
    for chunk in response:
        if not chunk.choices or not chunk.choices[0]:
            continue
        delta = chunk.choices[0].delta
        if delta is None:
            continue
        token = getattr(delta, "content", None)
        if token:
            yield token


def build_prompt_from_nodes(
    query: str,
    nodes: Sequence,
    *,
    memory_context: MemoryContext | None = None,
    intermediate_answers: Sequence[dict] | None = None,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> str:
    snippets = collect_context_snippets(nodes, max_context_chars=max_context_chars)
    context = "\n\n".join(
        f"[来源 {snippet.index}] {format_source_label(snippet.source)}\n{snippet.text}"
        for snippet in snippets
    )
    if not context:
        context = "未检索到可用知识库上下文。"

    return (
        "请基于以下知识库上下文回答用户问题。\n\n"
        f"{format_memory_context(memory_context)}"
        f"{format_intermediate_answers(intermediate_answers)}"
        "【知识库上下文】\n"
        f"{context}\n\n"
        "【回答要求】\n"
        "1. 优先使用知识库上下文，不要编造。\n"
        "2. 如果依据不足，说明缺少哪些信息。\n"
        "3. 对复杂问题，优先综合中间答案，再结合原始证据补充细节。\n"
        "4. 重要结论后可以标注来源编号，例如：[来源 1]。\n\n"
        f"【用户问题】\n{query}"
    )


def synthesize_intermediate_answers(
    sub_question_results: Sequence[dict],
    *,
    memory_context: MemoryContext | None = None,
    max_context_chars: int = 3000,
) -> list[dict]:
    """Generate one grounded intermediate answer for each sub-question."""

    answers: list[dict] = []
    for item in sub_question_results or []:
        question = str(item.get("query") or "").strip()
        nodes = item.get("nodes") or []
        if not question:
            continue
        answer = synthesize_single_intermediate_answer(
            question,
            nodes,
            memory_context=memory_context,
            max_context_chars=max_context_chars,
        )
        answers.append(
            {
                "index": item.get("index"),
                "question": question,
                "answer": answer,
                "source_count": len(nodes),
                "sources": [
                    SourceNodePayload.from_node(node).to_api_dict()
                    for node in sort_nodes_by_score(nodes)[:3]
                ],
            }
        )
    return answers


def synthesize_single_intermediate_answer(
    question: str,
    nodes: Sequence,
    *,
    memory_context: MemoryContext | None = None,
    max_context_chars: int = 3000,
) -> str:
    snippets = collect_context_snippets(nodes, max_context_chars=max_context_chars)
    context = "\n\n".join(
        f"[来源 {snippet.index}] {format_source_label(snippet.source)}\n{snippet.text}"
        for snippet in snippets
    )
    if not context:
        return "未找到足够证据回答该子问题。"

    prompt = (
        "请只基于给定证据回答子问题，回答要简洁，并在关键结论后标注来源编号。\n\n"
        f"{format_memory_context(memory_context, max_messages=4)}"
        f"【子问题】\n{question}\n\n"
        f"【证据】\n{context}\n\n"
        "【回答要求】\n"
        "1. 不要使用证据之外的信息。\n"
        "2. 如果证据不足，直接说明缺少依据。\n"
        "3. 输出 1-3 句话。\n"
    )
    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_api_base,
        timeout=LLM_REQUEST_TIMEOUT_SECONDS,
    )
    response = client.chat.completions.create(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": "你是企业知识库 RAG 的证据归纳助手。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        stream=False,
    )
    return (response.choices[0].message.content or "").strip()


def format_intermediate_answers(intermediate_answers: Sequence[dict] | None) -> str:
    if not intermediate_answers:
        return ""
    lines = ["【子问题中间答案】"]
    for item in intermediate_answers:
        index = item.get("index") or len(lines)
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if question or answer:
            lines.append(f"{index}. 子问题：{question}")
            lines.append(f"   中间答案：{answer}")
    return "\n".join(lines) + "\n\n"


def build_fallback_answer_from_intermediate(intermediate_answers: Sequence[dict] | None) -> str:
    """Build a deterministic answer when final LLM synthesis is unavailable."""

    if not intermediate_answers:
        return ""

    lines = [
        "最终生成模型暂时无响应，下面先基于已完成的子问题中间答案给出保守回答：",
        "",
    ]
    for item in intermediate_answers:
        index = item.get("index") or ""
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not answer:
            continue
        if question:
            lines.append(f"{index}. {question}")
        lines.append(answer)
        lines.append("")

    lines.append("说明：以上内容来自子问题检索与中间综合结果；最终综合生成阶段未完成，因此没有扩展缺少证据支撑的信息。")
    return "\n".join(lines).strip()


def collect_context_snippets(
    nodes: Sequence,
    *,
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS,
) -> list[ContextSnippet]:
    snippets: list[ContextSnippet] = []
    used_chars = 0
    seen_texts: set[str] = set()

    for node in sort_nodes_by_score(nodes):
        text = extract_node_text(node).strip()
        if not text:
            continue
        normalized = " ".join(text.split())
        if normalized in seen_texts:
            continue
        remaining = max_context_chars - used_chars
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[:remaining]
        seen_texts.add(normalized)
        source = SourceNodePayload.from_node(node)
        snippets.append(ContextSnippet(index=len(snippets) + 1, text=text, source=source))
        used_chars += len(text)

    return snippets


def sort_nodes_by_score(nodes: Sequence) -> list:
    return sorted(
        list(nodes or []),
        key=lambda item: getattr(item, "score", 0.0) or 0.0,
        reverse=True,
    )


def extract_node_text(node) -> str:
    if hasattr(node, "text"):
        return str(getattr(node, "text") or "")
    inner_node = getattr(node, "node", None)
    if inner_node is not None and hasattr(inner_node, "text"):
        return str(getattr(inner_node, "text") or "")
    return str(node or "")


def format_source_label(source: SourceNodePayload) -> str:
    parts = []
    if source.file_name:
        parts.append(f"文档={source.file_name}")
    elif source.source_path:
        parts.append(f"路径={source.source_path}")
    if source.parent_page_range:
        parts.append(f"页码={source.parent_page_range}")
    if source.parent_section_title:
        parts.append(f"章节={source.parent_section_title}")
    if source.chunk_id:
        parts.append(f"chunk={source.chunk_id}")
    return "；".join(parts) or "未知来源"


def format_memory_context(
    memory_context: MemoryContext | None,
    *,
    max_messages: int = DEFAULT_MAX_HISTORY_TURNS * 2,
) -> str:
    if memory_context is None or not memory_context.has_context:
        return ""

    lines = []
    if memory_context.long_term_memories:
        lines.append("【长期记忆】")
        for memory in memory_context.long_term_memories[:5]:
            content = str(memory or "").strip()
            if content:
                lines.append(f"- {content[:500]}")
        lines.append("")
    if memory_context.session_summary:
        lines.append("【历史摘要】")
        lines.append(memory_context.session_summary[:1200])
        lines.append("")

    recent_lines = []
    for message in memory_context.recent_messages[-max_messages:]:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "").strip()
        if content:
            recent_lines.append(f"{role}: {content[:500]}")
    if recent_lines:
        lines.append("【最近对话】")
        lines.extend(recent_lines)
        lines.append("")
    if not lines:
        return ""
    return "\n".join(lines) + "\n"
