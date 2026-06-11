from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import aiohttp

from app.config import settings as app_settings
from app.tools.models import ToolCallResult


class WebSearchTool:
    """企业级网页搜索工具适配器 - 支持多种搜索提供商"""

    name = "web_search"
    
    # 支持的搜索提供商
    SUPPORTED_PROVIDERS = {"mcp", "serpapi", "bing", "duckduckgo", "google"}

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        provider: str | None = None,
        default_top_k: int | None = None,
        api_key: str | None = None,
    ) -> None:
        self.enabled = app_settings.web_search_enabled if enabled is None else enabled
        self.provider = (app_settings.web_search_provider if provider is None else provider).lower()
        self.default_top_k = app_settings.web_search_top_k if default_top_k is None else default_top_k
        self.api_key = api_key or getattr(app_settings, 'web_search_api_key', None)
        
        # 验证提供商
        if self.provider not in self.SUPPORTED_PROVIDERS:
            raise ValueError(f"Unsupported web search provider: {self.provider}. Must be one of {self.SUPPORTED_PROVIDERS}")

    def __call__(self, arguments: Dict[str, Any], user: Any | None = None) -> ToolCallResult:
        """同步调用搜索"""
        query = str(arguments.get("query") or "").strip()
        top_k = self._coerce_top_k(arguments.get("top_k"))
        return asyncio.run(self.search(query=query, top_k=top_k))

    async def arun(self, arguments: Dict[str, Any], user: Any | None = None) -> ToolCallResult:
        """Async registry entrypoint."""

        query = str(arguments.get("query") or "").strip()
        top_k = self._coerce_top_k(arguments.get("top_k"))
        return await self.search(query=query, top_k=top_k)

    async def search(self, *, query: str, top_k: int | None = None) -> ToolCallResult:
        """异步执行搜索"""
        query = (query or "").strip()
        if not query:
            return ToolCallResult(
                tool_name=self.name,
                success=False,
                error="query_required",
                content="搜索查询不能为空",
            )
        
        if not self.enabled:
            return ToolCallResult(
                tool_name=self.name,
                success=False,
                content="Web Search tool is disabled. Configure WEB_SEARCH_ENABLED=true to enable it.",
                raw={"provider": self.provider, "query": query, "top_k": top_k or self.default_top_k},
                error="tool_not_configured",
            )
        
        try:
            results = await self._search_with_provider(query=query, top_k=top_k or self.default_top_k)
            return self._build_result(results)
        except Exception as exc:
            return ToolCallResult(
                tool_name=self.name,
                success=False,
                content=f"搜索失败: {str(exc)}",
                raw={"provider": self.provider, "query": query, "top_k": top_k or self.default_top_k},
                error=f"search_failed:{exc}",
            )

    async def _search_with_provider(self, *, query: str, top_k: int) -> List[Dict[str, Any]]:
        """根据提供商执行搜索"""
        if self.provider == "serpapi":
            return await self._search_with_serpapi(query=query, top_k=top_k)
        elif self.provider == "bing":
            return await self._search_with_bing(query=query, top_k=top_k)
        elif self.provider == "duckduckgo":
            return await self._search_with_duckduckgo(query=query, top_k=top_k)
        elif self.provider == "google":
            return await self._search_with_google(query=query, top_k=top_k)
        elif self.provider == "mcp":
            return await self._search_with_mcp(query=query, top_k=top_k)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    async def _search_with_serpapi(self, *, query: str, top_k: int) -> List[Dict[str, Any]]:
        """使用SerpAPI搜索"""
        if not self.api_key:
            raise ValueError("SERPAPI_API_KEY is required")
        
        url = "https://serpapi.com/search"
        params = {
            "q": query,
            "num": top_k,
            "api_key": self.api_key,
            "engine": "google",
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                data = await response.json()
                results = data.get("organic_results", [])
                return [self._parse_serpapi_result(r) for r in results[:top_k]]

    def _parse_serpapi_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析SerpAPI结果"""
        return {
            "title": result.get("title", ""),
            "link": result.get("link", ""),
            "snippet": result.get("snippet", ""),
            "source": "serpapi",
        }

    async def _search_with_bing(self, *, query: str, top_k: int) -> List[Dict[str, Any]]:
        """使用Bing搜索API"""
        if not self.api_key:
            raise ValueError("BING_API_KEY is required")
        
        url = "https://api.bing.microsoft.com/v7.0/search"
        headers = {"Ocp-Apim-Subscription-Key": self.api_key}
        params = {"q": query, "count": top_k}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as response:
                data = await response.json()
                results = data.get("webPages", {}).get("value", [])
                return [self._parse_bing_result(r) for r in results[:top_k]]

    def _parse_bing_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析Bing结果"""
        return {
            "title": result.get("name", ""),
            "link": result.get("url", ""),
            "snippet": result.get("snippet", ""),
            "source": "bing",
        }

    async def _search_with_duckduckgo(self, *, query: str, top_k: int) -> List[Dict[str, Any]]:
        """使用DuckDuckGo搜索"""
        url = "https://api.duckduckgo.com/"
        params = {
            "q": query,
            "format": "json",
            "no_redirect": "1",
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                data = await response.json()
                results = data.get("RelatedTopics", [])
                return [self._parse_duckduckgo_result(r) for r in results[:top_k]]

    def _parse_duckduckgo_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析DuckDuckGo结果"""
        return {
            "title": result.get("Text", ""),
            "link": result.get("FirstURL", ""),
            "snippet": result.get("Text", ""),
            "source": "duckduckgo",
        }

    async def _search_with_google(self, *, query: str, top_k: int) -> List[Dict[str, Any]]:
        """使用Google Custom Search API"""
        if not self.api_key:
            raise ValueError("GOOGLE_API_KEY is required")
        
        cx = getattr(app_settings, 'google_search_cx', None)
        if not cx:
            raise ValueError("GOOGLE_SEARCH_CX is required")
        
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "q": query,
            "num": top_k,
            "key": self.api_key,
            "cx": cx,
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                data = await response.json()
                results = data.get("items", [])
                return [self._parse_google_result(r) for r in results[:top_k]]

    def _parse_google_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析Google搜索结果"""
        return {
            "title": result.get("title", ""),
            "link": result.get("link", ""),
            "snippet": result.get("snippet", ""),
            "source": "google",
        }

    async def _search_with_mcp(self, *, query: str, top_k: int) -> List[Dict[str, Any]]:
        """使用MCP搜索"""
        # MCP客户端调用将在配置完成后实现
        from app.tools.mcp_client import build_mcp_client
        
        try:
            client = build_mcp_client()
            result = await client.call_tool("web_search", {"query": query, "top_k": top_k})
            await client.close()
            
            if result.success:
                return result.raw.get("results", [])
            else:
                raise ValueError(result.error)
        except Exception as exc:
            if getattr(app_settings, "web_search_mock_enabled", False):
                return self._get_mock_results(query, top_k)
            raise ValueError(f"MCP web search unavailable: {exc}") from exc

    def _get_mock_results(self, query: str, top_k: int) -> List[Dict[str, Any]]:
        """获取模拟搜索结果（用于开发测试）"""
        return [
            {
                "title": f"搜索结果 {i+1}: {query}",
                "link": f"https://example.com/result/{i+1}",
                "snippet": f"这是关于 '{query}' 的搜索结果 {i+1} 的摘要信息。",
                "source": "mock",
            }
            for i in range(top_k)
        ]

    def _build_result(self, results: List[Dict[str, Any]]) -> ToolCallResult:
        """构建工具调用结果"""
        content = "\n\n".join([
            f"【{i+1}】{r.get('title', '')}\n链接: {r.get('link', '')}\n摘要: {r.get('snippet', '')}"
            for i, r in enumerate(results)
        ])
        
        return ToolCallResult(
            tool_name=self.name,
            success=True,
            content=content,
            raw={
                "provider": self.provider,
                "results": results,
                "count": len(results),
            },
            usage={"results_count": len(results)},
        )

    def _coerce_top_k(self, value: Any) -> int:
        """强制转换top_k参数"""
        try:
            top_k = int(value)
        except (TypeError, ValueError):
            top_k = self.default_top_k
        return max(1, min(top_k, 10))


# 添加配置到settings
def add_web_search_settings(settings):
    """添加网页搜索相关配置"""
    settings.web_search_api_key = getattr(settings, 'web_search_api_key', "")
    settings.google_search_cx = getattr(settings, 'google_search_cx', "")
    settings.mcp_server_url = getattr(settings, 'mcp_server_url', "")
    settings.mcp_api_key = getattr(settings, 'mcp_api_key', "")
    return settings
