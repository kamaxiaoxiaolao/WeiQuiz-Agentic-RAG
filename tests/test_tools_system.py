import pytest

import app.tools.planner as planner_module
from app.config import settings as app_settings
from app.tools.models import (
    ToolCallResult,
    ToolCategory,
    ToolInputSchema,
    ToolParameter,
    ToolPermission,
    ToolSpec,
    ToolType,
)
from app.tools.planner import plan_tool_call
from app.tools.registry import ToolRegistry, build_default_tool_registry
from app.tools.web_search import WebSearchTool


class MockUser:
    id = "u1"
    role = "user"


def _echo_spec() -> ToolSpec:
    return ToolSpec(
        name="echo_tool",
        version="1.0.0",
        description="Echo arguments",
        tool_type=ToolType.LOCAL,
        permission=ToolPermission.USER,
        category=ToolCategory.UTILITY,
        input_schema=ToolInputSchema(
            properties={
                "query": ToolParameter(name="query", type="string", required=True),
                "top_k": ToolParameter(name="top_k", type="integer", required=False, default=5),
            },
            required=["query"],
        ),
    )


def test_registry_validates_arguments_and_fills_defaults():
    registry = ToolRegistry()

    def handler(arguments, user=None):
        return ToolCallResult(tool_name="echo_tool", success=True, raw=arguments)

    registry.register(_echo_spec(), handler)

    result = registry.call("echo_tool", {"query": "hello", "top_k": "3"}, user=MockUser())

    assert result.success
    assert result.raw == {"query": "hello", "top_k": 3}

    missing = registry.call("echo_tool", {"top_k": 3}, user=MockUser())
    assert not missing.success
    assert missing.error == "missing_required_argument:query"

    invalid = registry.call("echo_tool", {"query": "hello", "top_k": "bad"}, user=MockUser())
    assert not invalid.success
    assert invalid.error == "invalid_argument_type:top_k:integer"


@pytest.mark.asyncio
async def test_registry_calls_async_handlers_without_nested_event_loop():
    registry = ToolRegistry()

    async def handler(arguments, user=None):
        return ToolCallResult(tool_name="echo_tool", success=True, content=arguments["query"])

    registry.register_async(_echo_spec(), handler)

    result = await registry.call_async("echo_tool", {"query": "hello"}, user=MockUser())

    assert result.success
    assert result.content == "hello"


def test_tool_planner_falls_back_when_llm_planning_fails(monkeypatch):
    class FailingGateway:
        def chat_completion(self, **kwargs):
            raise RuntimeError("planner unavailable")

    monkeypatch.setattr(planner_module, "get_llm_gateway", lambda: FailingGateway())
    registry = build_default_tool_registry()

    plan = plan_tool_call(
        query="今天 AI 有什么新闻？",
        registry=registry,
        allowed_tool_names=["web_search"],
    )

    assert plan.tool_name == "web_search"
    assert plan.arguments == {"query": "今天 AI 有什么新闻？"}
    assert plan.method == "rule_fallback"
    assert "tool_planner_failed" in plan.error


def test_default_registry_wires_memory_search_tool():
    registry = build_default_tool_registry()

    result = registry.call("memory_search", {"query": "我的偏好", "limit": "2"}, user=MockUser())

    assert result.success
    assert result.raw["query"] == "我的偏好"
    assert result.raw["limit"] == 2
    assert "memories" in result.raw


@pytest.mark.asyncio
async def test_web_search_mock_results_require_explicit_flag(monkeypatch):
    monkeypatch.setattr(app_settings, "web_search_mock_enabled", False)
    tool = WebSearchTool(enabled=True, provider="mcp")

    result = await tool.search(query="latest ai news", top_k=2)

    assert not result.success
    assert result.error.startswith("search_failed:")

    monkeypatch.setattr(app_settings, "web_search_mock_enabled", True)
    result = await tool.search(query="latest ai news", top_k=2)

    assert result.success
    assert result.raw["provider"] == "mcp"
    assert result.raw["results"][0]["source"] == "mock"
