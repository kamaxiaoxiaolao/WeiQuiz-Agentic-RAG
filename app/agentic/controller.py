from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum

from openai import OpenAI

from app.config import settings
from app.agentic.router import IntentType, QueryStrategy, RouteResult, route_query
from app.tools.planner import ToolCallPlan, plan_tool_call
from app.tools.registry import ToolRegistry


class AgentMode(str, Enum):
    CHITCHAT = "chitchat"
    RAG_WORKFLOW = "rag_workflow"
    TOOL_CALL = "tool_call"
    CLARIFICATION = "clarification"


@dataclass(frozen=True)
class MemoryPolicy:
    use_recent_messages: bool = True
    use_session_summary: bool = True
    use_long_term_memory: bool = True
    long_term_top_k: int = 3


@dataclass(frozen=True)
class ClarificationDecision:
    needed: bool = False
    question: str = ""
    reason: str = ""
    missing_slots: tuple[str, ...] = ()
    method: str = "none"


@dataclass
class AgentDecision:
    mode: AgentMode
    route: RouteResult
    memory_policy: MemoryPolicy
    clarification: ClarificationDecision = ClarificationDecision()
    tool_plan: ToolCallPlan | None = None
    rag_strategy: QueryStrategy = QueryStrategy.DIRECT
    need_grounding: bool = False
    max_retries: int = 1
    reason: str = ""


class AgentController:
    """Decision layer for Agentic RAG.

    The controller decides the execution path and planning metadata. It does
    not execute tools, retrieve documents, read/write memory, or generate the
    final answer.
    """

    def __init__(self, tool_registry: ToolRegistry):
        self.tool_registry = tool_registry

    def decide(self, query: str) -> AgentDecision:
        route = route_query(query)
        clarification = self._clarification_decision(query, route)
        mode = AgentMode.CLARIFICATION if clarification.needed else self._mode_for_route(route)
        memory_policy = self._memory_policy_for_mode(mode)
        tool_plan = None

        if mode == AgentMode.TOOL_CALL:
            tool_plan = plan_tool_call(
                query=query,
                registry=self.tool_registry,
                allowed_tool_names=route.tools or [],
            )

        return AgentDecision(
            mode=mode,
            route=route,
            memory_policy=memory_policy,
            clarification=clarification,
            tool_plan=tool_plan,
            rag_strategy=route.query_strategy,
            need_grounding=route.need_grounding,
            max_retries=self._max_retries_for_route(route),
            reason=route.reason,
        )

    @staticmethod
    def _mode_for_route(route: RouteResult) -> AgentMode:
        if route.intent == IntentType.CHITCHAT:
            return AgentMode.CHITCHAT
        if route.intent in {IntentType.WEB_SEARCH, IntentType.SQL_QUERY}:
            return AgentMode.TOOL_CALL
        return AgentMode.RAG_WORKFLOW

    @staticmethod
    def _memory_policy_for_mode(mode: AgentMode) -> MemoryPolicy:
        if mode == AgentMode.CHITCHAT:
            return MemoryPolicy(
                use_recent_messages=True,
                use_session_summary=True,
                use_long_term_memory=True,
                long_term_top_k=5,
            )
        if mode == AgentMode.TOOL_CALL:
            return MemoryPolicy(
                use_recent_messages=True,
                use_session_summary=True,
                use_long_term_memory=True,
                long_term_top_k=2,
            )
        return MemoryPolicy(
            use_recent_messages=True,
            use_session_summary=True,
            use_long_term_memory=True,
            long_term_top_k=3,
        )

    @staticmethod
    def _max_retries_for_route(route: RouteResult) -> int:
        # Keep the control surface, but default to one retry until evaluation
        # data proves that extra rewrite/retrieval rounds are worth the cost.
        return 1

    def _clarification_decision(self, query: str, route: RouteResult) -> ClarificationDecision:
        query = (query or "").strip()
        if not query or route.intent == IntentType.CHITCHAT:
            return ClarificationDecision()

        rule_result = self._rule_based_clarification(query)
        if rule_result.needed:
            return rule_result

        return self._llm_based_clarification(query, route)

    @staticmethod
    def _rule_based_clarification(query: str) -> ClarificationDecision:
        compact = re.sub(r"\s+", "", query)
        vague_reference = ("这个", "这两个", "那个", "它", "他们", "上面", "刚才")

        if any(key in compact for key in ("对比这两个", "比较这两个")):
            return ClarificationDecision(
                needed=True,
                question="你想让我对比哪两个对象？请补充对象名称或对应文档范围。",
                reason="missing_comparison_targets",
                missing_slots=("comparison_targets",),
                method="rule",
            )
        if "哪个更好" in compact and not re.search(r"和|与|还是|对比|比较", compact):
            return ClarificationDecision(
                needed=True,
                question="你想比较哪些候选项？评价标准是性能、成本、稳定性，还是其他维度？",
                reason="missing_candidates_and_criteria",
                missing_slots=("candidates", "criteria"),
                method="rule",
            )
        if any(phrase in compact for phrase in ("总结这个", "分析这个", "处理这个问题")):
            return ClarificationDecision(
                needed=True,
                question="这里的“这个”具体指什么？请补充文档、问题对象或上下文范围。",
                reason="ambiguous_reference",
                missing_slots=("referent",),
                method="rule",
            )
        if any(ref in compact for ref in vague_reference) and len(compact) <= 12:
            return ClarificationDecision(
                needed=True,
                question="你这里指的是哪个对象或哪段内容？请补充一下上下文。",
                reason="short_ambiguous_reference",
                missing_slots=("referent",),
                method="rule",
            )
        return ClarificationDecision()

    @staticmethod
    def _llm_based_clarification(query: str, route: RouteResult) -> ClarificationDecision:
        prompt = f"""你是 Agentic RAG 的澄清判断器。
只判断用户问题是否缺少关键条件，导致不适合直接检索或调用工具。

如果问题可以直接进入知识库检索、工具调用或闲聊，返回 needed=false。
只有在对象、范围、评价标准、关键约束明显缺失时，才返回 needed=true。

请只输出 JSON：
{{
  "needed": false,
  "question": "",
  "reason": "",
  "missing_slots": []
}}

用户问题：{query}
当前路由：intent={route.intent.value}, strategy={route.query_strategy.value}
"""
        try:
            client = OpenAI(
                api_key=settings.qwen_llm_api_key,
                base_url=settings.router_api_base,
                timeout=settings.router_timeout_seconds,
                max_retries=0,
            )
            response = client.chat.completions.create(
                model=getattr(settings, "router_model", None) or settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=160,
            )
            content = response.choices[0].message.content or ""
            parsed = AgentController._parse_json_object(content)
            needed = bool(parsed.get("needed", False))
            question = str(parsed.get("question") or "").strip()
            if needed and question:
                missing_slots = parsed.get("missing_slots") or []
                if not isinstance(missing_slots, list):
                    missing_slots = []
                return ClarificationDecision(
                    needed=True,
                    question=question,
                    reason=str(parsed.get("reason") or "llm_clarification"),
                    missing_slots=tuple(str(slot) for slot in missing_slots),
                    method="llm",
                )
        except Exception:
            return ClarificationDecision()
        return ClarificationDecision()

    @staticmethod
    def _parse_json_object(content: str) -> dict:
        content = (content or "").strip()
        if "```" in content:
            match = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
            if match:
                content = match.group(1).strip()
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            content = content[start : end + 1]
        return json.loads(content)
