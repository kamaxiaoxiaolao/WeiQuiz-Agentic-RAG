"""Sub-question decomposition for multi-hop Agentic RAG.

The module keeps decomposition as a safe enhancement: if the LLM call fails or
returns unusable JSON, the workflow falls back to the original query.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class DecompositionResult:
    original: str
    sub_questions: list[str] = field(default_factory=list)
    reason: str = ""
    method: str = "fallback"
    success: bool = True


DECOMPOSE_PROMPT = """You are the query planning module for an enterprise RAG system.

Split the user question into 2-4 retrieval-focused sub-questions only when the
question requires comparison, multiple entities, temporal reasoning, or multiple
facts from different passages. Each sub-question must be independently
retrievable from a knowledge base.

For Chinese comparison questions containing words like "比较", "分别", "共同点",
"差异", or questions that mention multiple entities, prefer decomposing into:
1. one retrieval question per entity/aspect;
2. one synthesis question for comparison or conclusion.

Return strict JSON only:
{{"sub_questions": ["question 1", "question 2"], "reason": "why decomposition helps"}}

If decomposition is not useful, return one sub-question equal to the original
question.

User question:
{query}
"""


def decompose_question(query: str, max_questions: int = 4) -> DecompositionResult:
    """Decompose a complex query into retrieval-ready sub-questions."""

    llm_result = llm_decompose(query, max_questions=max_questions)
    if llm_result is not None and _is_useful_decomposition(query, llm_result.sub_questions):
        return llm_result
    return fallback_decompose(query, max_questions=max_questions)


def llm_decompose(query: str, max_questions: int = 4) -> Optional[DecompositionResult]:
    if not query.strip():
        return DecompositionResult(original=query, sub_questions=[], reason="empty query")

    try:
        client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_api_base)
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "user", "content": DECOMPOSE_PROMPT.format(query=query)}],
            temperature=0,
            max_tokens=320,
        )
        content = response.choices[0].message.content or ""
        payload = _parse_json(content)
        sub_questions = _clean_sub_questions(
            payload.get("sub_questions", []),
            original=query,
            max_questions=max_questions,
        )
        if not sub_questions:
            return None
        return DecompositionResult(
            original=query,
            sub_questions=sub_questions,
            reason=str(payload.get("reason") or ""),
            method="llm",
            success=True,
        )
    except Exception as exc:
        logger.warning("Sub-question decomposition failed, fallback used: %s", exc)
        return None


def fallback_decompose(query: str, max_questions: int = 4) -> DecompositionResult:
    """Rule-based fallback for obvious comparison/list questions."""

    text = query.strip()
    if not text:
        return DecompositionResult(original=query, sub_questions=[], reason="empty query")

    entity_questions = _decompose_comparison_entities(text, max_questions=max_questions)
    if entity_questions:
        return DecompositionResult(
            original=query,
            sub_questions=entity_questions,
            reason="rule-based entity/aspect split for comparison query",
            method="fallback_entity_comparison",
            success=True,
        )

    separators = [
        r"\bcompare\b",
        r"\bdifference between\b",
        r"\bversus\b",
        r"\bvs\.?\b",
        r"对比",
        r"比较",
        r"区别",
        r"差异",
        r"分别",
        r"以及",
        r"和",
        r"与",
        r"、",
    ]
    pattern = "|".join(f"(?:{sep})" for sep in separators)
    parts = [part.strip(" ?？,，。") for part in re.split(pattern, text) if part.strip()]

    if 2 <= len(parts) <= max_questions:
        sub_questions = [part if part.endswith(("?", "？")) else f"{part}是什么？" for part in parts]
        return DecompositionResult(
            original=query,
            sub_questions=_clean_sub_questions(sub_questions, original=query, max_questions=max_questions),
            reason="rule-based split for comparison/list query",
            method="fallback_rules",
            success=True,
        )

    return DecompositionResult(
        original=query,
        sub_questions=[text],
        reason="single-hop fallback",
        method="fallback_single",
        success=True,
    )


def _is_useful_decomposition(query: str, sub_questions: list[str]) -> bool:
    """Treat a single original-question echo as an unusable decomposition."""

    if len(sub_questions) >= 2:
        return True
    if not sub_questions:
        return False
    return _normalize_question(sub_questions[0]) != _normalize_question(query)


def _normalize_question(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower().strip(" ?？,，。"))


def _decompose_comparison_entities(text: str, max_questions: int) -> list[str]:
    """Build stable Chinese/English comparison sub-questions from named entities.

    This is intentionally conservative: it only fires for obvious comparison
    prompts and extracts title-like English entities such as "Chloroplast" or
    "East New York". It gives the workflow a useful retrieval plan when the LLM
    returns the original question unchanged.
    """

    comparison_markers = ("比较", "对比", "共同点", "差异", "区别", "分别", "compare", "difference")
    if not any(marker.lower() in text.lower() for marker in comparison_markers):
        return []

    entity_pattern = r"\b[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,4}\b"
    candidates = [match.group(0).strip() for match in re.finditer(entity_pattern, text)]
    stopwords = {"RAG", "Agentic", "JSON", "LLM", "API"}

    entities: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in stopwords:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        entities.append(candidate)
        if len(entities) >= max(2, max_questions - 1):
            break

    if len(entities) < 2:
        return []

    questions = [f"{entity} 在材料中体现出的核心变化过程、原因和关键表现是什么？" for entity in entities]
    if len(questions) < max_questions:
        joined = " 和 ".join(entities[:2])
        questions.append(f"{joined} 体现出的变化过程有哪些共同点和差异？")
    return questions[:max_questions]


def _parse_json(content: str) -> dict:
    content = content.strip()
    if "```" in content:
        match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
        if match:
            content = match.group(1).strip()

    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1:
        content = content[start : end + 1]
    return json.loads(content)


def _clean_sub_questions(
    values: object,
    *,
    original: str,
    max_questions: int,
) -> list[str]:
    if not isinstance(values, list):
        return [original.strip()] if original.strip() else []

    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        question = str(value or "").strip()
        if not question:
            continue
        normalized = question.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(question)
        if len(cleaned) >= max_questions:
            break
    return cleaned or ([original.strip()] if original.strip() else [])
