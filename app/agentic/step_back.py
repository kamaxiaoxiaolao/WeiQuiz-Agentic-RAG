"""Step-back query transformation for background-aware retrieval."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

STEP_BACK_TIMEOUT_SECONDS = 45


@dataclass
class StepBackResult:
    original_query: str
    step_back_question: str
    method: str
    reason: str
    success: bool = True
    error: str = ""


STEP_BACK_PROMPT = """你是企业知识库 RAG 的 Step-back 查询转换模块。

请将用户的具体问题改写成一个更上位、更通用、适合检索背景知识的问题。
它要帮助系统先找到相关概念、机制、流程或影响因素，再辅助回答原问题。

要求：
1. 只输出一个 step-back question，不要输出解释或 JSON。
2. 保留用户问题所属主题，不要改成无关的大而泛问题。
3. 不要回答原问题。
4. 问题应适合在知识库文档中检索。

用户问题：
{query}
"""


def generate_step_back_question(query: str) -> StepBackResult:
    """Generate a broader background question for retrieval."""

    original = query.strip()
    if not original:
        return StepBackResult(
            original_query=query,
            step_back_question=query,
            method="fallback_original",
            reason="empty query",
            success=False,
        )

    try:
        client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base,
            timeout=STEP_BACK_TIMEOUT_SECONDS,
        )
        response = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": "你负责为 RAG 检索生成 step-back background question。"},
                {"role": "user", "content": STEP_BACK_PROMPT.format(query=original)},
            ],
            temperature=0,
            max_tokens=180,
            stream=False,
        )
        question = (response.choices[0].message.content or "").strip()
        if not question:
            raise ValueError("empty step-back question")
        return StepBackResult(
            original_query=original,
            step_back_question=question,
            method="llm",
            reason="retrieve broader background knowledge before final synthesis",
        )
    except Exception as exc:
        logger.warning("Step-back transform failed, fallback to original query: %s", exc)
        return StepBackResult(
            original_query=original,
            step_back_question=original,
            method="fallback_original",
            reason="Step-back transform failed",
            success=False,
            error=str(exc),
        )
