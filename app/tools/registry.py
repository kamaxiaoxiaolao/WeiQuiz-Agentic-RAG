from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class ToolType(str, Enum):
    LOCAL = "local"
    API = "api"
    MCP = "mcp"


class ToolPermission(str, Enum):
    USER = "user"
    ADMIN = "admin"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    tool_type: ToolType = ToolType.LOCAL
    permission: ToolPermission = ToolPermission.USER


@dataclass
class ToolCallResult:
    tool_name: str
    success: bool
    content: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_ms: float = 0.0


ToolHandler = Callable[[dict[str, Any], Any | None], ToolCallResult | str | dict[str, Any]]


class ToolRegistry:
    """Internal tool registry for Agentic RAG tool management.

    The registry defines the application's tool contract. A tool can be backed
    by a local function, an HTTP API, or an MCP client adapter.
    """

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, ToolHandler] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool already registered: {spec.name}")
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler

    def list_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def get_spec(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def call(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        user: Any | None = None,
    ) -> ToolCallResult:
        spec = self._specs.get(name)
        handler = self._handlers.get(name)
        if spec is None or handler is None:
            return ToolCallResult(
                tool_name=name,
                success=False,
                error=f"tool not registered: {name}",
            )
        if not self._has_permission(spec, user):
            return ToolCallResult(
                tool_name=name,
                success=False,
                error=f"permission denied for tool: {name}",
            )

        started = time.perf_counter()
        try:
            result = handler(arguments or {}, user)
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            return self._normalize_result(name, result, duration_ms)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            return ToolCallResult(
                tool_name=name,
                success=False,
                error=str(exc),
                duration_ms=duration_ms,
            )

    @staticmethod
    def _has_permission(spec: ToolSpec, user: Any | None) -> bool:
        if spec.permission == ToolPermission.USER:
            return True
        return getattr(user, "role", None) == "admin"

    @staticmethod
    def _normalize_result(name: str, result: ToolCallResult | str | dict[str, Any], duration_ms: float) -> ToolCallResult:
        if isinstance(result, ToolCallResult):
            result.duration_ms = duration_ms
            return result
        if isinstance(result, dict):
            return ToolCallResult(
                tool_name=name,
                success=bool(result.get("success", True)),
                content=str(result.get("content", "")),
                raw=result,
                error=str(result.get("error", "")),
                duration_ms=duration_ms,
            )
        return ToolCallResult(
            tool_name=name,
            success=True,
            content=str(result),
            duration_ms=duration_ms,
        )


def build_default_tool_registry() -> ToolRegistry:
    from app.tools.web_search import WebSearchTool

    registry = ToolRegistry()
    registry.register(_kb_search_spec(), _not_wired_handler("kb_search", "kb_search is executed by the current RAG workflow."))
    registry.register(_web_search_spec(), WebSearchTool())
    registry.register(_sql_query_spec(), _not_wired_handler("sql_query", "sql_query tool is not connected yet."))
    registry.register(
        _memory_search_spec(),
        _not_wired_handler("memory_search", "memory_search is currently injected through MemoryContext."),
    )
    return registry


def _not_wired_handler(tool_name: str, message: str) -> ToolHandler:
    def handler(arguments: dict[str, Any], user: Any | None = None) -> ToolCallResult:
        return ToolCallResult(
            tool_name=tool_name,
            success=False,
            content=message,
            raw={"arguments": arguments},
            error="tool_not_wired",
        )

    return handler


def _kb_search_spec() -> ToolSpec:
    return ToolSpec(
        name="kb_search",
        description="Search the local enterprise knowledge base with the Agentic RAG workflow.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        tool_type=ToolType.LOCAL,
        permission=ToolPermission.USER,
    )


def _web_search_spec() -> ToolSpec:
    return ToolSpec(
        name="web_search",
        description="Search external web information for recent or out-of-knowledge-base questions.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        tool_type=ToolType.MCP,
        permission=ToolPermission.USER,
    )


def _sql_query_spec() -> ToolSpec:
    return ToolSpec(
        name="sql_query",
        description="Query structured business data through a controlled SQL tool.",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "tables": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["question"],
        },
        tool_type=ToolType.MCP,
        permission=ToolPermission.ADMIN,
    )


def _memory_search_spec() -> ToolSpec:
    return ToolSpec(
        name="memory_search",
        description="Search user-scoped long-term semantic memory.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        tool_type=ToolType.LOCAL,
        permission=ToolPermission.USER,
    )
