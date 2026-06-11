from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Awaitable, Optional, Dict, List, Iterable

from app.tools.models import (
    ToolSpec,
    ToolCallResult,
    ToolPermission,
    ToolType,
    ToolCategory,
    ToolInputSchema,
    ToolParameter,
    ToolHandler,
    AsyncToolHandler,
)


class ToolRegistry:
    """企业级工具注册中心 - 支持异步调用、MCP集成和Skill系统"""

    def __init__(self) -> None:
        self._specs: Dict[str, ToolSpec] = {}
        self._handlers: Dict[str, ToolHandler | AsyncToolHandler] = {}
        self._async_handlers: set[str] = set()

    def register(
        self,
        spec: ToolSpec,
        handler: ToolHandler | AsyncToolHandler,
        is_async: bool = False,
    ) -> None:
        """注册工具"""
        if spec.name in self._specs:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._specs[spec.name] = spec
        self._handlers[spec.name] = handler
        if is_async:
            self._async_handlers.add(spec.name)

    def register_async(self, spec: ToolSpec, handler: AsyncToolHandler) -> None:
        """注册异步工具"""
        self.register(spec, handler, is_async=True)

    def list_specs(
        self,
        tool_type: Optional[ToolType] = None,
        permission: Optional[ToolPermission] = None,
        category: Optional[str] = None,
        include_deprecated: bool = False,
    ) -> List[ToolSpec]:
        """列出工具规格，支持过滤"""
        specs = list(self._specs.values())
        
        if not include_deprecated:
            specs = [s for s in specs if not s.deprecated]
        if tool_type:
            specs = [s for s in specs if s.tool_type == tool_type]
        if permission:
            specs = [s for s in specs if s.permission == permission]
        if category:
            specs = [s for s in specs if s.category.value == category]
        
        return specs

    def get_spec(self, name: str) -> Optional[ToolSpec]:
        """获取工具规格"""
        return self._specs.get(name)

    def is_async(self, name: str) -> bool:
        """检查工具是否为异步"""
        return name in self._async_handlers

    def call(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        user: Optional[Any] = None,
        max_retries: int = 0,
        timeout_ms: Optional[int] = None,
    ) -> ToolCallResult:
        """同步调用工具"""
        spec = self._specs.get(name)
        handler = self._handlers.get(name)
        
        if spec is None or handler is None:
            return ToolCallResult(
                tool_name=name,
                success=False,
                error=f"tool_not_registered:{name}",
            )
        
        if not self._has_permission(spec, user):
            return ToolCallResult(
                tool_name=name,
                success=False,
                error=f"permission_denied:{name}",
            )

        prepared_args, validation_error = self._prepare_arguments(spec, arguments or {})
        if validation_error:
            return ToolCallResult(
                tool_name=name,
                success=False,
                error=validation_error,
                raw={"arguments": arguments or {}},
            )

        if self.is_async(name):
            # 异步工具同步调用
            try:
                return asyncio.run(self._call_async_internal(name, prepared_args, user, max_retries, timeout_ms))
            except Exception as exc:
                return ToolCallResult(
                    tool_name=name,
                    success=False,
                    error=f"async_call_failed:{exc}",
                )

        return self._call_sync_internal(name, spec, handler, prepared_args, user, max_retries, timeout_ms)

    async def call_async(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        *,
        user: Optional[Any] = None,
        max_retries: int = 0,
        timeout_ms: Optional[int] = None,
    ) -> ToolCallResult:
        """异步调用工具"""
        spec = self._specs.get(name)
        handler = self._handlers.get(name)
        
        if spec is None or handler is None:
            return ToolCallResult(
                tool_name=name,
                success=False,
                error=f"tool_not_registered:{name}",
            )
        
        if not self._has_permission(spec, user):
            return ToolCallResult(
                tool_name=name,
                success=False,
                error=f"permission_denied:{name}",
            )

        prepared_args, validation_error = self._prepare_arguments(spec, arguments or {})
        if validation_error:
            return ToolCallResult(
                tool_name=name,
                success=False,
                error=validation_error,
                raw={"arguments": arguments or {}},
            )

        if self.is_async(name):
            return await self._call_async_internal(name, prepared_args, user, max_retries, timeout_ms)
        
        # 同步工具异步调用
        return await asyncio.to_thread(
            self._call_sync_internal, name, spec, handler, prepared_args, user, max_retries, timeout_ms
        )

    def _call_sync_internal(
        self,
        name: str,
        spec: ToolSpec,
        handler: ToolHandler,
        arguments: Optional[Dict[str, Any]],
        user: Optional[Any],
        max_retries: int,
        timeout_ms: Optional[int],
    ) -> ToolCallResult:
        """内部同步调用逻辑"""
        args = arguments or {}
        last_error: Exception | None = None
        
        for attempt in range(max_retries + 1):
            started = time.perf_counter()
            try:
                result = handler(args, user)
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                return self._normalize_result(name, result, duration_ms)
            except Exception as exc:
                last_error = exc
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                if attempt < max_retries:
                    time.sleep(min(0.5 * (2 ** attempt), 2.0))
        
        return ToolCallResult(
            tool_name=name,
            success=False,
            error=str(last_error) if last_error else "unknown_error",
            duration_ms=duration_ms if 'duration_ms' in locals() else 0.0,
        )

    async def _call_async_internal(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]],
        user: Optional[Any],
        max_retries: int,
        timeout_ms: Optional[int],
    ) -> ToolCallResult:
        """内部异步调用逻辑"""
        spec = self._specs.get(name)
        handler = self._handlers.get(name)
        if not spec or not handler:
            return ToolCallResult(tool_name=name, success=False, error="tool_not_found")
        
        args = arguments or {}
        last_error: Exception | None = None
        
        for attempt in range(max_retries + 1):
            started = time.perf_counter()
            try:
                coro = handler(args, user)
                if timeout_ms:
                    result = await asyncio.wait_for(coro, timeout=timeout_ms / 1000)
                else:
                    result = await coro
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                return self._normalize_result(name, result, duration_ms)
            except asyncio.TimeoutError:
                last_error = TimeoutError(f"Tool call timeout after {timeout_ms}ms")
            except Exception as exc:
                last_error = exc
            
            if attempt < max_retries:
                await asyncio.sleep(min(0.5 * (2 ** attempt), 2.0))
        
        duration_ms = round((time.perf_counter() - started) * 1000, 2) if 'started' in locals() else 0.0
        return ToolCallResult(
            tool_name=name,
            success=False,
            error=str(last_error) if last_error else "unknown_error",
            duration_ms=duration_ms,
        )

    @staticmethod
    def _has_permission(spec: ToolSpec, user: Optional[Any]) -> bool:
        """检查用户是否有权限调用工具"""
        if spec.permission == ToolPermission.PUBLIC:
            return True
        if spec.permission == ToolPermission.USER:
            return user is not None
        if spec.permission == ToolPermission.ADMIN:
            return getattr(user, "role", None) == "admin"
        return False

    @staticmethod
    def _prepare_arguments(spec: ToolSpec, arguments: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
        """Validate tool arguments and fill defaults from ToolSpec."""

        prepared: Dict[str, Any] = {}
        properties = spec.input_schema.properties or {}
        required = set(spec.input_schema.required or [])

        for name, param in properties.items():
            value = arguments.get(name, None)
            if value is None:
                if param.default is not None:
                    value = param.default
                elif param.required or name in required:
                    return {}, f"missing_required_argument:{name}"
                else:
                    continue

            coerced, error = ToolRegistry._coerce_argument(name, value, param.type)
            if error:
                return {}, error
            if param.enum and coerced not in param.enum:
                return {}, f"invalid_enum_argument:{name}"
            prepared[name] = coerced

        for name in required:
            if name not in prepared:
                return {}, f"missing_required_argument:{name}"

        return prepared, ""

    @staticmethod
    def _coerce_argument(name: str, value: Any, expected_type: str) -> tuple[Any, str]:
        if expected_type == "any":
            return value, ""
        if expected_type == "string":
            if isinstance(value, (dict, list)):
                return None, f"invalid_argument_type:{name}:string"
            return str(value), ""
        if expected_type == "integer":
            if isinstance(value, bool):
                return None, f"invalid_argument_type:{name}:integer"
            try:
                return int(value), ""
            except (TypeError, ValueError):
                return None, f"invalid_argument_type:{name}:integer"
        if expected_type == "number":
            if isinstance(value, bool):
                return None, f"invalid_argument_type:{name}:number"
            try:
                return float(value), ""
            except (TypeError, ValueError):
                return None, f"invalid_argument_type:{name}:number"
        if expected_type == "boolean":
            if isinstance(value, bool):
                return value, ""
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes", "y"}:
                    return True, ""
                if lowered in {"false", "0", "no", "n"}:
                    return False, ""
            return None, f"invalid_argument_type:{name}:boolean"
        if expected_type == "array":
            if isinstance(value, list):
                return value, ""
            return None, f"invalid_argument_type:{name}:array"
        if expected_type == "object":
            if isinstance(value, dict):
                return value, ""
            return None, f"invalid_argument_type:{name}:object"
        return None, f"invalid_argument_type:{name}:{expected_type}"

    @staticmethod
    def _normalize_result(name: str, result: Any, duration_ms: float) -> ToolCallResult:
        """规范化工具调用结果"""
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

    def get_tool_info(self, name: str) -> Optional[Dict[str, Any]]:
        """获取工具详细信息"""
        spec = self._specs.get(name)
        if not spec:
            return None
        return {
            "name": spec.name,
            "version": spec.version,
            "description": spec.description,
            "tool_type": spec.tool_type.value,
            "permission": spec.permission.value,
            "category": spec.category.value,
            "tags": spec.tags,
            "deprecated": spec.deprecated,
            "is_async": self.is_async(name),
        }

    def to_openai_tools(self, allowed_names: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        """转换为OpenAI工具调用格式"""
        specs = self.list_specs()
        if allowed_names:
            allowed = set(allowed_names)
            specs = [s for s in specs if s.name in allowed]
        return [spec.to_openai_format() for spec in specs]

    def to_mcp_tools(self) -> List[Dict[str, Any]]:
        """转换为MCP工具格式"""
        return [spec.to_mcp_format() for spec in self.list_specs()]


def build_default_tool_registry() -> ToolRegistry:
    """构建默认工具注册中心"""
    from app.tools.web_search import WebSearchTool
    
    registry = ToolRegistry()
    
    registry.register(_kb_search_spec(), _not_wired_handler("kb_search"))
    web_search_tool = WebSearchTool()
    registry.register_async(_web_search_spec(), web_search_tool.arun)
    registry.register(_sql_query_spec(), _not_wired_handler("sql_query"))
    registry.register(_memory_search_spec(), _memory_search_handler())
    
    return registry


def _not_wired_handler(tool_name: str) -> ToolHandler:
    """创建未连接工具的处理器"""
    def handler(arguments: Dict[str, Any], user: Optional[Any] = None) -> ToolCallResult:
        return ToolCallResult(
            tool_name=tool_name,
            success=False,
            content=f"{tool_name} is not wired yet.",
            raw={"arguments": arguments},
            error="tool_not_wired",
        )
    return handler


def _memory_search_handler() -> ToolHandler:
    """Create a user-scoped long-term memory search handler."""

    def handler(arguments: Dict[str, Any], user: Optional[Any] = None) -> ToolCallResult:
        from app.services.long_term_memory_service import LongTermMemoryService

        query = str(arguments.get("query") or "").strip()
        limit = int(arguments.get("limit") or 5)
        user_id = str(getattr(user, "id", "") or "")
        memories = LongTermMemoryService().search(user_id=user_id, query=query, limit=limit)
        content = "\n".join(f"- {memory}" for memory in memories) if memories else "未找到相关长期记忆。"
        return ToolCallResult(
            tool_name="memory_search",
            success=True,
            content=content,
            raw={"query": query, "limit": limit, "memories": memories},
            usage={"memory_count": len(memories)},
        )

    return handler


def _kb_search_spec() -> ToolSpec:
    """知识库搜索工具规格"""
    return ToolSpec(
        name="kb_search",
        version="1.0.0",
        description="Search the local enterprise knowledge base with the Agentic RAG workflow.",
        tool_type=ToolType.LOCAL,
        permission=ToolPermission.USER,
        category=ToolCategory.SEARCH,
        input_schema=ToolInputSchema(
            type="object",
            properties={
                "query": ToolParameter(
                    name="query",
                    type="string",
                    description="搜索查询",
                    required=True,
                ),
                "top_k": ToolParameter(
                    name="top_k",
                    type="integer",
                    description="返回结果数",
                    required=False,
                    default=5,
                ),
            },
            required=["query"],
        ),
        tags=["knowledge", "search", "rag"],
    )


def _web_search_spec() -> ToolSpec:
    """网页搜索工具规格"""
    return ToolSpec(
        name="web_search",
        version="1.0.0",
        description="Search external web information for recent or out-of-knowledge-base questions.",
        tool_type=ToolType.MCP,
        permission=ToolPermission.USER,
        category=ToolCategory.SEARCH,
        input_schema=ToolInputSchema(
            type="object",
            properties={
                "query": ToolParameter(
                    name="query",
                    type="string",
                    description="搜索查询",
                    required=True,
                ),
                "top_k": ToolParameter(
                    name="top_k",
                    type="integer",
                    description="返回结果数",
                    required=False,
                    default=5,
                ),
            },
            required=["query"],
        ),
        tags=["web", "search", "external"],
    )


def _sql_query_spec() -> ToolSpec:
    """SQL查询工具规格"""
    return ToolSpec(
        name="sql_query",
        version="1.0.0",
        description="Query structured business data through a controlled SQL tool.",
        tool_type=ToolType.MCP,
        permission=ToolPermission.ADMIN,
        category=ToolCategory.DATA,
        input_schema=ToolInputSchema(
            type="object",
            properties={
                "question": ToolParameter(
                    name="question",
                    type="string",
                    description="自然语言问题",
                    required=True,
                ),
                "tables": ToolParameter(
                    name="tables",
                    type="array",
                    description="表名列表",
                    required=False,
                ),
            },
            required=["question"],
        ),
        tags=["database", "sql", "data"],
    )


def _memory_search_spec() -> ToolSpec:
    """记忆搜索工具规格"""
    return ToolSpec(
        name="memory_search",
        version="1.0.0",
        description="Search user-scoped long-term semantic memory.",
        tool_type=ToolType.LOCAL,
        permission=ToolPermission.USER,
        category=ToolCategory.DATA,
        input_schema=ToolInputSchema(
            type="object",
            properties={
                "query": ToolParameter(
                    name="query",
                    type="string",
                    description="搜索查询",
                    required=True,
                ),
                "limit": ToolParameter(
                    name="limit",
                    type="integer",
                    description="返回结果数",
                    required=False,
                    default=5,
                ),
            },
            required=["query"],
        ),
        tags=["memory", "user", "semantic"],
    )
