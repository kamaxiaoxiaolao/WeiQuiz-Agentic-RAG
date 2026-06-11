from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from app.config import settings
from app.tools.models import (
    ToolCallResult,
    ToolSpec,
    ToolType,
    ToolPermission,
    ToolCategory,
)

logger = logging.getLogger(__name__)


class MCPError(Exception):
    """MCP协议错误"""
    pass


class MCPClient:
    """MCP (Model Context Protocol) 客户端适配器 - 企业级实现"""
    
    def __init__(
        self,
        server_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: int = 30,
    ):
        self.server_url = server_url or settings.mcp_server_url if hasattr(settings, 'mcp_server_url') else ""
        self.api_key = api_key or settings.mcp_api_key if hasattr(settings, 'mcp_api_key') else ""
        self.timeout_seconds = timeout_seconds
        self._session: Optional[aiohttp.ClientSession] = None
        self._registered_tools: Dict[str, ToolSpec] = {}
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建HTTP会话"""
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        return self._session
    
    async def discover(self) -> List[ToolSpec]:
        """发现MCP服务器上的所有工具"""
        if not self.server_url:
            logger.warning("MCP server URL not configured")
            return []
        
        try:
            session = await self._get_session()
            async with session.get(f"{self.server_url}/tools") as response:
                if response.status != 200:
                    logger.error(f"MCP discovery failed: {response.status}")
                    return []
                
                data = await response.json()
                tools = data.get("tools", [])
                return [self._parse_tool_spec(tool_data) for tool_data in tools]
        except Exception as exc:
            logger.error(f"MCP discovery error: {exc}")
            return []
    
    def _parse_tool_spec(self, data: Dict[str, Any]) -> ToolSpec:
        """解析MCP工具规格"""
        return ToolSpec(
            name=data.get("name", ""),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            tool_type=ToolType.MCP,
            permission=self._parse_permission(data.get("metadata", {})),
            category=self._parse_category(data.get("metadata", {})),
            input_schema=data.get("input_schema", {}),
            output_schema=data.get("output_schema", {}),
            tags=data.get("metadata", {}).get("tags", []),
            deprecated=data.get("metadata", {}).get("deprecated", False),
        )
    
    def _parse_permission(self, metadata: Dict[str, Any]) -> ToolPermission:
        """解析权限级别"""
        perm = metadata.get("permission", "user").upper()
        try:
            return ToolPermission[perm]
        except KeyError:
            return ToolPermission.USER
    
    def _parse_category(self, metadata: Dict[str, Any]) -> ToolCategory:
        """解析工具分类"""
        cat = metadata.get("category", "custom").upper()
        try:
            return ToolCategory[cat]
        except KeyError:
            return ToolCategory.CUSTOM
    
    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        user: Optional[Any] = None,
    ) -> ToolCallResult:
        """调用MCP工具"""
        if not self.server_url:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error="mcp_server_not_configured",
                content="MCP server URL is not configured",
            )
        
        try:
            session = await self._get_session()
            payload = {
                "tool_name": tool_name,
                "arguments": arguments,
                "context": self._build_context(user),
            }
            
            async with session.post(
                f"{self.server_url}/call",
                json=payload,
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"MCP call failed: {response.status} - {error_text}")
                    return ToolCallResult(
                        tool_name=tool_name,
                        success=False,
                        error=f"mcp_server_error:{response.status}",
                        content=error_text,
                    )
                
                data = await response.json()
                return self._parse_tool_result(tool_name, data)
        
        except asyncio.TimeoutError:
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error="mcp_timeout",
                content="MCP server timeout",
            )
        except Exception as exc:
            logger.error(f"MCP call error: {exc}")
            return ToolCallResult(
                tool_name=tool_name,
                success=False,
                error=f"mcp_client_error:{exc}",
                content=str(exc),
            )
    
    def _build_context(self, user: Optional[Any]) -> Dict[str, Any]:
        """构建调用上下文"""
        context = {}
        if user:
            context["user_id"] = getattr(user, "id", None)
            context["user_role"] = getattr(user, "role", None)
        return context
    
    def _parse_tool_result(self, tool_name: str, data: Dict[str, Any]) -> ToolCallResult:
        """解析工具调用结果"""
        return ToolCallResult(
            tool_name=tool_name,
            success=bool(data.get("success", False)),
            content=data.get("content", ""),
            raw=data.get("raw", {}),
            error=data.get("error", ""),
            duration_ms=data.get("duration_ms", 0.0),
            usage=data.get("usage", {}),
        )
    
    async def register_to_registry(self, registry) -> None:
        """将MCP工具注册到本地工具注册表"""
        tools = await self.discover()
        for tool_spec in tools:
            handler = self._create_mcp_handler(tool_spec.name)
            registry.register(tool_spec, handler, is_async=True)
            self._registered_tools[tool_spec.name] = tool_spec
            logger.info(f"Registered MCP tool: {tool_spec.name}")
    
    def _create_mcp_handler(self, tool_name: str):
        """创建MCP工具处理器"""
        async def handler(arguments: Dict[str, Any], user: Optional[Any] = None) -> ToolCallResult:
            return await self.call_tool(tool_name, arguments, user)
        return handler
    
    async def close(self) -> None:
        """关闭HTTP会话"""
        if self._session:
            await self._session.close()
            self._session = None


class LocalMCPAdapter:
    """本地MCP适配器 - 将本地工具转换为MCP兼容格式"""
    
    def __init__(self, registry):
        self.registry = registry
    
    def get_tools(self) -> List[Dict[str, Any]]:
        """获取所有工具的MCP格式"""
        return self.registry.to_mcp_tools()
    
    def call_tool(self, tool_name: str, arguments: Dict[str, Any], context: Dict[str, Any] = None) -> Dict[str, Any]:
        """调用工具"""
        user = self._parse_user_from_context(context)
        result = self.registry.call(tool_name, arguments, user=user)
        return result.model_dump()
    
    def _parse_user_from_context(self, context: Optional[Dict[str, Any]]) -> Any:
        """从上下文中解析用户信息"""
        if not context:
            return None
        
        class MockUser:
            id = context.get("user_id")
            role = context.get("user_role")
        
        return MockUser()


def build_mcp_client() -> MCPClient:
    """构建MCP客户端"""
    return MCPClient()
