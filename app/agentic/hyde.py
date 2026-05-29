"""HyDE query transformation for semantic retrieval enhancement."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

HYDE_TIMEOUT_SECONDS = 45


@dataclass
class HyDEResult:
    original_query: str
    hypothetical_document: str
    retrieval_query: str
    method: str
    reason: str
    success: bool = True
    error: str = ""


HYDE_PROMPT = """你是企业知识库 RAG 的 HyDE 查询转换模块。

请根据用户问题，生成一段“可能出现在知识库资料中的说明性文档片段”，
用于提升向量检索召回。

要求：
1. 输出一段 80-180 字的说明性文本，不要输出标题、列表或 JSON。
2. 使用知识库文档风格，尽量包含相关概念、机制、原因和术语。
3. 不要编造具体数字、日期、来源名或无法确认的专有事实。
4. 这不是最终答案，只是用于检索的 hypothetical document。

用户问题：
{query}
"""


def generate_hyde_query(query: str) -> HyDEResult:
    """Generate a hypothetical document and use it as retrieval query."""

    original = query.strip()
    if not original:
        return HyDEResult(
            original_query=query,
            hypothetical_document="",
            retrieval_query=query,
            method="fallback_original",
            reason="empty query",
            success=False,
        )

    try:
        client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base,
            timeout=HYDE_TIMEOUT_SECONDS,
        )
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": "你负责生成用于检索增强的 hypothetical document。"},
                {"role": "user", "content": HYDE_PROMPT.format(query=original)},
            ],
            temperature=0.2,
            max_tokens=320,
            stream=False,
        )
        document = (response.choices[0].message.content or "").strip()
        if not document:
            raise ValueError("empty HyDE document")
        return HyDEResult(
            original_query=original,
            hypothetical_document=document,
            retrieval_query=document,
            method="llm",
            reason="use hypothetical document for semantic retrieval",
        )
    except Exception as exc:
        logger.warning("HyDE transform failed, fallback to original query: %s", exc)
        return HyDEResult(
            original_query=original,
            hypothetical_document="",
            retrieval_query=original,
            method="fallback_original",
            reason="HyDE transform failed",
            success=False,
            error=str(exc),
        )
