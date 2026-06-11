from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.llm import LLMTask, get_llm_gateway
from app.tools.registry import ToolRegistry, ToolSpec


@dataclass
class ToolCallPlan:
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    method: str = "function_calling"
    error: str = ""

    @property
    def has_tool_call(self) -> bool:
        return bool(self.tool_name)


def plan_tool_call(
    *,
    query: str,
    registry: ToolRegistry,
    allowed_tool_names: Iterable[str] | None = None,
) -> ToolCallPlan:
    """Ask the router LLM to choose a tool and generate tool arguments."""

    specs = _filter_specs(registry, allowed_tool_names)
    if not specs:
        return ToolCallPlan(error="no_allowed_tools")

    try:
        response = get_llm_gateway().chat_completion(
            task=LLMTask.TOOL_PLANNER,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是 WeiQuiz Agentic RAG 的工具规划器。"
                        "请根据用户问题和可用工具，选择一个最合适的工具并生成参数。"
                        "如果可用工具能够解决问题，必须使用 tool call；不要直接回答用户问题。"
                    ),
                },
                {"role": "user", "content": query},
            ],
            tools=[_to_openai_tool(spec) for spec in specs],
            tool_choice="auto",
            temperature=0,
            max_tokens=128,
        )
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            return _fallback_plan(query, specs, "llm_returned_no_tool_call")

        function = tool_calls[0].function
        arguments = json.loads(function.arguments or "{}")
        if not isinstance(arguments, dict):
            return ToolCallPlan(error="tool_arguments_not_object")
        return ToolCallPlan(tool_name=function.name, arguments=arguments)
    except json.JSONDecodeError as exc:
        return _fallback_plan(query, specs, f"tool_arguments_json_error:{exc}")
    except Exception as exc:
        return _fallback_plan(query, specs, f"tool_planner_failed:{exc}")


def _filter_specs(registry: ToolRegistry, allowed_tool_names: Iterable[str] | None) -> list[ToolSpec]:
    allowed = {name for name in (allowed_tool_names or []) if name}
    specs = registry.list_specs()
    if allowed:
        specs = [spec for spec in specs if spec.name in allowed]
    return specs


def _to_openai_tool(spec: ToolSpec) -> dict[str, Any]:
    return spec.to_openai_format()


def _fallback_plan(query: str, specs: list[ToolSpec], error: str) -> ToolCallPlan:
    """Build deterministic arguments when tool planning LLM is unavailable."""

    if len(specs) != 1:
        return ToolCallPlan(error=error)

    spec = specs[0]
    if spec.name in {"web_search", "kb_search", "memory_search"}:
        return ToolCallPlan(
            tool_name=spec.name,
            arguments={"query": query},
            method="rule_fallback",
            error=error,
        )
    if spec.name == "sql_query":
        return ToolCallPlan(
            tool_name=spec.name,
            arguments={"question": query},
            method="rule_fallback",
            error=error,
        )
    return ToolCallPlan(error=error)
