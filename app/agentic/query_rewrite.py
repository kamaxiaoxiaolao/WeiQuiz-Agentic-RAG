"""Query Rewrite — LLM 改写问题以提升检索质量

当检索质量不足时，用 LLM 将用户原始问题改写成更适合检索的形式。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from app.llm import LLMTask, get_llm_gateway

logger = logging.getLogger(__name__)


# ============================================================
# 1. 改写结果
# ============================================================

@dataclass
class RewriteResult:
    """改写结果。"""
    original: str              # 原始问题
    rewritten: str             # 改写后的检索查询
    keywords: list[str]        # 提取的关键词
    method: str                # "llm" | "fallback"
    success: bool = True       # 是否改写成功


# ============================================================
# 2. LLM 改写
# ============================================================

REWRITE_PROMPT = """你是一个检索查询优化器。将用户问题改写成更适合知识库检索的形式。

## 改写规则

1. 去掉指代词（"这个"、"那个"、"它"）
2. 去掉口语化表达（"怎么弄"、"搞一下"）
3. 补充同义词和相关词
4. 保留核心名词和动词
5. 输出关键词列表，用于 BM25 检索

## 输出格式

只输出 JSON，不要输出其他内容：
{{"rewritten": "改写后的检索查询", "keywords": ["关键词1", "关键词2", "关键词3"]}}

## 用户问题

{query}"""


def llm_rewrite(query: str) -> Optional[RewriteResult]:
    """LLM 改写查询。"""
    prompt = REWRITE_PROMPT.format(query=query)

    try:
        response = get_llm_gateway().chat_completion(
            task=LLMTask.REWRITE,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200,
        )
        content = response.choices[0].message.content or ""

        # 解析 JSON
        result = _parse_response(content)
        rewritten = result.get("rewritten", "").strip()
        keywords = result.get("keywords", [])

        # 校验
        if not rewritten:
            logger.warning("LLM 返回空改写结果")
            return None

        # 如果改写结果和原始一样，也算成功（说明问题本身已经适合检索）
        return RewriteResult(
            original=query,
            rewritten=rewritten,
            keywords=keywords if keywords else [query],
            method="llm",
            success=True,
        )

    except Exception as e:
        logger.error("LLM 改写失败: %s", e)
        return None


def _parse_response(content: str) -> dict:
    """解析 LLM 响应，兼容多种格式。"""
    content = content.strip()

    # 去除 markdown code block
    if "```" in content:
        match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
        if match:
            content = match.group(1).strip()

    # 提取 JSON 部分
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1:
        content = content[start : end + 1]

    return json.loads(content)


# ============================================================
# 3. 兜底改写（规则）
# ============================================================

# 常见口语化替换表
COLLOQUIAL_MAP = {
    "怎么弄": "操作流程",
    "怎么搞": "操作方法",
    "搞一下": "操作步骤",
    "啥意思": "定义 含义",
    "咋回事": "原因 说明",
    "咋办": "解决方法 处理方式",
    "咋用": "使用方法 操作指南",
}


def fallback_rewrite(query: str) -> RewriteResult:
    """规则兜底改写（LLM 失败时使用）。"""
    rewritten = query.strip()

    # 替换口语化表达
    for colloquial, formal in COLLOQUIAL_MAP.items():
        rewritten = rewritten.replace(colloquial, formal)

    # 去掉常见指代词
    for word in ["这个", "那个", "这些", "那些", "一下"]:
        rewritten = rewritten.replace(word, "")

    # 清理多余空格
    rewritten = " ".join(rewritten.split())

    # 简单分词（按空格和标点）
    keywords = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', rewritten)

    return RewriteResult(
        original=query,
        rewritten=rewritten if rewritten else query,
        keywords=keywords if keywords else [query],
        method="fallback",
        success=True,
    )


# ============================================================
# 4. 对外接口
# ============================================================

def rewrite_query(query: str) -> RewriteResult:
    """改写查询入口。

    优先 LLM 改写，失败则规则兜底。
    """
    # 1. LLM 改写
    llm_result = llm_rewrite(query)
    if llm_result is not None:
        logger.info(
            "[Rewrite] llm | original='%s' | rewritten='%s' | keywords=%s",
            query[:50], llm_result.rewritten[:50], llm_result.keywords,
        )
        return llm_result

    # 2. 规则兜底
    fallback_result = fallback_rewrite(query)
    logger.info(
        "[Rewrite] fallback | original='%s' | rewritten='%s'",
        query[:50], fallback_result.rewritten[:50],
    )
    return fallback_result
