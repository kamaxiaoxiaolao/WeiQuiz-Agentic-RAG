"""Query Router for Agentic RAG.

The router decides whether a user query should go to knowledge-base RAG,
chitchat, multi-step RAG, web search, or structured SQL tools.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.config import settings
from app.llm import LLMTask, get_llm_gateway

logger = logging.getLogger(__name__)


class IntentType(str, Enum):
    """Supported query intents."""

    CHITCHAT = "chitchat"
    KNOWLEDGE_BASE = "knowledge_base"
    MULTI_STEP = "multi_step"
    WEB_SEARCH = "web_search"
    SQL_QUERY = "sql_query"


class QueryStrategy(str, Enum):
    """Query planning strategy selected by the unified analyzer."""

    DIRECT = "direct"
    DECOMPOSITION = "decomposition"
    HYDE = "hyde"
    STEP_BACK = "step_back"
    WEB_SEARCH = "web_search"
    SQL_QUERY = "sql_query"
    CHITCHAT = "chitchat"


@dataclass
class RouteResult:
    """Router result."""

    intent: IntentType
    method: str
    reason: str
    confidence: float = 1.0
    query_strategy: QueryStrategy = QueryStrategy.DIRECT
    complexity: str = "single_hop"
    tools: list[str] | None = None
    normalized_query: str = ""
    need_grounding: bool = False


CHITCHAT_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"^(你好|您好|hello|hi|hey)\s*[!！。,.，？?]*$",
        r"^(谢谢|感谢|thanks|thank you)\s*[!！。,.，？?]*$",
        r"^(再见|拜拜|bye|goodbye)\s*[!！。,.，？?]*$",
        r"^(你是谁|你能做什么|介绍一下你自己)\s*[!！。,.，？?]*$",
        r"^(在吗|还在吗|可以开始吗)\s*[!！。,.，？?]*$",
    ]
]

CHITCHAT_COMPACT_EXACT: set[str] = {
    "你好",
    "您好",
    "谢谢",
    "感谢",
    "再见",
    "拜拜",
    "你是谁",
    "你能做什么",
    "你可以做什么",
    "介绍一下你自己",
    "你好你能做什么",
    "你好你可以做什么",
}

CHITCHAT_COMPACT_CONTAINS: tuple[str, ...] = (
    "你能做什么",
    "你可以做什么",
    "你是谁",
    "介绍一下你自己",
)

MEMORY_CHAT_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"(请记住|帮我记住|记一下|你记住|remember that)",
        r"(我刚刚|我刚才|我前面|刚才|前面).{0,12}(说了什么|说过什么|提到什么|聊了什么)",
        r"(总结一下|概括一下|回顾一下).{0,12}(前面|刚才|刚刚|我们刚才|我们前面)",
        r"(我们刚才|我们前面|刚刚).{0,12}(说到哪|聊到哪|讲到哪)",
        r"(我让你记住了什么|你还记得什么|你记得我说过什么)",
    ]
]

MEMORY_WRITE_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"(请记住|帮我记住|记一下|你记住|remember that)",
    ]
]

SQL_STAT_KEYWORDS: set[str] = {
    "统计",
    "数量",
    "多少",
    "总数",
    "平均",
    "最大",
    "最小",
    "排行",
    "排名",
    "top",
    "count",
    "sum",
    "avg",
    "max",
    "min",
}

SQL_ENTITY_KEYWORDS: set[str] = {
    "用户",
    "订单",
    "题目",
    "考试",
    "成绩",
    "分数",
    "上传",
    "文档数",
    "访问量",
    "日志",
    "表",
    "数据库",
}

KNOWLEDGE_GUARD_WORDS: set[str] = {
    "资料",
    "知识库",
    "手册",
    "规范",
    "制度",
    "方案",
    "报告",
    "章节",
    "条款",
    "说明",
    "如何",
    "怎么",
    "文档",
    "课件",
}

WEB_KEYWORDS: set[str] = {
    "最新",
    "今天",
    "昨天",
    "最近",
    "新闻",
    "资讯",
    "实时",
    "当前",
    "现在",
    "发布",
    "行情",
    "官网",
}

WEB_KNOWLEDGE_GUARD_WORDS: set[str] = {
    "文档中",
    "知识库",
    "这份文档",
    "上传的文档",
    "资料里",
    "报告里",
}

MULTI_STEP_KEYWORDS: set[str] = {
    "对比",
    "比较",
    "分析",
    "总结",
    "归纳",
    "结合",
    "分别",
    "影响",
    "优缺点",
    "建议",
    "方案",
    "步骤",
    "流程",
}

MULTI_STEP_MIN_LENGTH = 18


def _has_any(text: str, keywords: set[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _rule_chitchat(query: str) -> Optional[RouteResult]:
    compact = re.sub(r"\s+", "", query or "").strip("。！？!?，,；;：:")
    if compact in CHITCHAT_COMPACT_EXACT or (
        len(compact) <= 14 and any(marker in compact for marker in CHITCHAT_COMPACT_CONTAINS)
    ):
        return RouteResult(
            IntentType.CHITCHAT,
            "rule",
            "命中闲聊或助手能力询问",
            query_strategy=QueryStrategy.CHITCHAT,
            complexity="simple",
            tools=[],
        )

    if (
        (
            any(pattern.search(query) for pattern in MEMORY_WRITE_PATTERNS)
            or (
                any(pattern.search(query) for pattern in MEMORY_CHAT_PATTERNS)
                and not _has_any(query, WEB_KEYWORDS)
            )
        )
        and not _has_any(query, KNOWLEDGE_GUARD_WORDS | SQL_ENTITY_KEYWORDS)
    ):
        return RouteResult(
            IntentType.CHITCHAT,
            "rule",
            "命中记忆写入或前文追问模式",
            query_strategy=QueryStrategy.CHITCHAT,
            complexity="simple",
            tools=[],
        )

    if len(query) <= 5 and not _has_any(
        query, SQL_STAT_KEYWORDS | WEB_KEYWORDS | MULTI_STEP_KEYWORDS | KNOWLEDGE_GUARD_WORDS
    ):
        return RouteResult(
            IntentType.CHITCHAT,
            "rule",
            "极短句且无业务关键词",
            query_strategy=QueryStrategy.CHITCHAT,
            complexity="simple",
            tools=[],
        )

    for pattern in CHITCHAT_PATTERNS:
        if pattern.match(query):
            return RouteResult(
                IntentType.CHITCHAT,
                "rule",
                "命中闲聊模式",
                query_strategy=QueryStrategy.CHITCHAT,
                complexity="simple",
                tools=[],
            )

    return None


def _rule_sql(query: str) -> Optional[RouteResult]:
    stat_hit = _has_any(query, SQL_STAT_KEYWORDS)
    entity_hit = _has_any(query, SQL_ENTITY_KEYWORDS)
    knowledge_context_hit = _has_any(query, {"文档中", "文档里", "知识库", "资料里", "报告里"})

    if stat_hit and entity_hit and not knowledge_context_hit:
        return RouteResult(
            IntentType.SQL_QUERY,
            "rule",
            "命中统计类数据库查询特征",
            query_strategy=QueryStrategy.SQL_QUERY,
            complexity="tool_required",
            tools=["sql_query"],
        )

    return None


def _rule_web(query: str) -> Optional[RouteResult]:
    if _has_any(query, WEB_KEYWORDS) and not _has_any(query, WEB_KNOWLEDGE_GUARD_WORDS):
        return RouteResult(
            IntentType.WEB_SEARCH,
            "rule",
            "命中时效性或外部实时信息特征",
            query_strategy=QueryStrategy.WEB_SEARCH,
            complexity="tool_required",
            tools=["web_search"],
        )

    return None


def _rule_multi_step(query: str) -> Optional[RouteResult]:
    if len(query) >= MULTI_STEP_MIN_LENGTH and _has_any(query, MULTI_STEP_KEYWORDS):
        return RouteResult(
            IntentType.MULTI_STEP,
            "rule",
            "命中复杂分析或多步骤推理特征",
            query_strategy=QueryStrategy.DECOMPOSITION,
            complexity="multi_step",
            tools=["kb_search"],
            need_grounding=True,
        )

    return None


def rule_based_route(query: str) -> Optional[RouteResult]:
    """Return a rule-based route result, or None when rules are inconclusive."""

    normalized_query = query.strip()
    if not normalized_query:
        return RouteResult(
            IntentType.CHITCHAT,
            "rule",
            "空问题",
            query_strategy=QueryStrategy.CHITCHAT,
            complexity="simple",
            tools=[],
        )

    for checker in (_rule_chitchat, _rule_sql, _rule_web, _rule_multi_step):
        result = checker(normalized_query)
        if result is not None:
            return result

    return None


LLM_ROUTE_PROMPT = """你是一个企业知识库 Agentic RAG 的 Unified Query Analyzer。

请一次性完成意图识别和检索策略选择。

intent 只能是：
- chitchat：闲聊、问候、感谢、告别、询问助手身份。
- knowledge_base：需要从企业知识库、上传文档、制度、手册、课件、报告中检索答案。
- multi_step：需要多步分析、比较、归纳、综合多个信息点后回答。
- web_search：需要最新、实时、外部互联网信息。
- sql_query：需要查询结构化数据库中的数量、统计、排行、平均值、明细列表。

query_strategy 只能是：
- direct：简单知识库问题，直接检索。
- decomposition：多实体、多条件、比较、总结、多跳问题，先拆子问题。
- hyde：问题抽象、术语不明确、直接语义检索可能召回差。
- step_back：问题过于具体，需要先检索上位概念或背景知识。
- web_search：需要外部实时信息。
- sql_query：需要结构化数据库查询。
- chitchat：闲聊直答。

选择规则：
- 多实体对比、分别说明、共同点/差异，优先 decomposition。
- 抽象问题、用户不知道准确术语，优先 hyde。
- 过于具体但需要背景概念，优先 step_back。
- 最新、当前、新闻、价格、官网等实时外部信息，优先 web_search。
- 数量、统计、排行、销售额、订单、数据库字段，优先 sql_query。

只返回 JSON，不要输出多余文字：
{{
  "intent": "knowledge_base",
  "query_strategy": "direct",
  "complexity": "simple|single_hop|multi_step|tool_required",
  "tools": ["kb_search"],
  "normalized_query": "",
  "need_grounding": false,
  "confidence": 0.8,
  "reasoning": "不超过30字的理由"
}}

用户问题：
{query}
"""


def _parse_llm_response(content: str) -> dict:
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


def _normalize_enum_value(value: object) -> str:
    return str(value or "").strip().lower()


def llm_based_route(query: str) -> RouteResult:
    """Classify intent through an OpenAI-compatible LLM endpoint."""

    prompt = LLM_ROUTE_PROMPT.format(query=query)

    try:
        response = get_llm_gateway().chat_completion(
            task=LLMTask.ROUTER,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,
        )
        content = response.choices[0].message.content or ""
        parsed = _parse_llm_response(content)

        intent_str = _normalize_enum_value(parsed.get("intent"))
        reasoning = parsed.get("reasoning", "")

        try:
            intent = IntentType(intent_str)
        except ValueError:
            logger.warning("Invalid LLM route intent '%s', fallback to knowledge_base", intent_str)
            return RouteResult(
                IntentType.KNOWLEDGE_BASE,
                "llm",
                f"LLM 返回未知意图: {intent_str}",
                confidence=0.5,
                query_strategy=QueryStrategy.DIRECT,
                tools=["kb_search"],
            )

        strategy_str = _normalize_enum_value(parsed.get("query_strategy")) or _default_strategy_for_intent(intent).value
        try:
            strategy = QueryStrategy(strategy_str)
        except ValueError:
            strategy = _default_strategy_for_intent(intent)

        tools = parsed.get("tools")
        if not isinstance(tools, list):
            tools = _default_tools_for_strategy(strategy)

        confidence = parsed.get("confidence", 0.8)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.8

        return RouteResult(
            intent,
            "llm",
            reasoning or "LLM 统一查询分析",
            confidence=confidence_value,
            query_strategy=strategy,
            complexity=str(parsed.get("complexity") or _default_complexity_for_strategy(strategy)),
            tools=[str(tool) for tool in tools],
            normalized_query=str(parsed.get("normalized_query") or ""),
            need_grounding=bool(parsed.get("need_grounding", strategy == QueryStrategy.DECOMPOSITION)),
        )

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse LLM route JSON: %s", exc)
        return RouteResult(IntentType.KNOWLEDGE_BASE, "llm", f"JSON 解析失败: {exc}", confidence=0.5)

    except Exception as exc:
        logger.error("LLM route failed: %s", exc)
        return RouteResult(IntentType.KNOWLEDGE_BASE, "fallback", "LLM 路由失败，降级为知识库检索", confidence=0.3)


def route_query(query: str) -> RouteResult:
    """Route a user query with rule-first and LLM-fallback strategy."""

    rule_result = rule_based_route(query)
    if rule_result is not None:
        logger.info(
            "[Router] rule | intent=%s | reason=%s | query=%s",
            rule_result.intent.value,
            rule_result.reason,
            query[:80],
        )
        return rule_result

    if not getattr(settings, "router_llm_enabled", False):
        fallback_result = RouteResult(
            IntentType.KNOWLEDGE_BASE,
            "heuristic",
            "LLM Router disabled; fallback to direct knowledge-base retrieval",
            confidence=0.6,
            query_strategy=QueryStrategy.DIRECT,
            complexity="single_hop",
            tools=["kb_search"],
        )
        logger.info(
            "[Router] heuristic | intent=%s | reason=%s | query=%s",
            fallback_result.intent.value,
            fallback_result.reason,
            query[:80],
        )
        return fallback_result

    llm_result = llm_based_route(query)
    logger.info(
        "[Router] %s | intent=%s | reason=%s | query=%s",
        llm_result.method,
        llm_result.intent.value,
        llm_result.reason,
        query[:80],
    )
    return llm_result


def _default_strategy_for_intent(intent: IntentType) -> QueryStrategy:
    mapping = {
        IntentType.CHITCHAT: QueryStrategy.CHITCHAT,
        IntentType.KNOWLEDGE_BASE: QueryStrategy.DIRECT,
        IntentType.MULTI_STEP: QueryStrategy.DECOMPOSITION,
        IntentType.WEB_SEARCH: QueryStrategy.WEB_SEARCH,
        IntentType.SQL_QUERY: QueryStrategy.SQL_QUERY,
    }
    return mapping[intent]


def _default_tools_for_strategy(strategy: QueryStrategy) -> list[str]:
    mapping = {
        QueryStrategy.CHITCHAT: [],
        QueryStrategy.DIRECT: ["kb_search"],
        QueryStrategy.DECOMPOSITION: ["kb_search"],
        QueryStrategy.HYDE: ["kb_search"],
        QueryStrategy.STEP_BACK: ["kb_search"],
        QueryStrategy.WEB_SEARCH: ["web_search"],
        QueryStrategy.SQL_QUERY: ["sql_query"],
    }
    return mapping[strategy]


def _default_complexity_for_strategy(strategy: QueryStrategy) -> str:
    if strategy == QueryStrategy.DECOMPOSITION:
        return "multi_step"
    if strategy in {QueryStrategy.WEB_SEARCH, QueryStrategy.SQL_QUERY}:
        return "tool_required"
    if strategy == QueryStrategy.CHITCHAT:
        return "simple"
    return "single_hop"
