from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from openai import OpenAI

from app.config import settings
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

    client = OpenAI(
        api_key=settings.qwen_llm_api_key,
        base_url=settings.router_api_base,
    )

    try:
        response = client.chat.completions.create(
            model=getattr(settings, "router_model", None) or settings.llm_model,
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
            return ToolCallPlan(error="llm_returned_no_tool_call")

        function = tool_calls[0].function
        arguments = json.loads(function.arguments or "{}")
        if not isinstance(arguments, dict):
            return ToolCallPlan(error="tool_arguments_not_object")
        return ToolCallPlan(tool_name=function.name, arguments=arguments)
    except json.JSONDecodeError as exc:
        return ToolCallPlan(error=f"tool_arguments_json_error:{exc}")
    except Exception as exc:
        return ToolCallPlan(error=f"tool_planner_failed:{exc}")


def _filter_specs(registry: ToolRegistry, allowed_tool_names: Iterable[str] | None) -> list[ToolSpec]:
    allowed = {name for name in (allowed_tool_names or []) if name}
    specs = registry.list_specs()
    if allowed:
        specs = [spec for spec in specs if spec.name in allowed]
    return specs


def _to_openai_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.input_schema,
        },
    }
