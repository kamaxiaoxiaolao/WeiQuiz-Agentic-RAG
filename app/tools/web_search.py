from __future__ import annotations

from typing import Any

from app.config import settings as app_settings
from app.tools.registry import ToolCallResult


class WebSearchTool:
    """Adapter for external web search tools.

    The first implementation is intentionally configuration-gated. It defines
    the stable tool contract before wiring a concrete MCP server or search API.
    """

    name = "web_search"

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        provider: str | None = None,
        default_top_k: int | None = None,
    ) -> None:
        self.enabled = app_settings.web_search_enabled if enabled is None else enabled
        self.provider = (app_settings.web_search_provider if provider is None else provider).lower()
        self.default_top_k = app_settings.web_search_top_k if default_top_k is None else default_top_k

    def __call__(self, arguments: dict[str, Any], user: Any | None = None) -> ToolCallResult:
        query = str(arguments.get("query") or "").strip()
        top_k = self._coerce_top_k(arguments.get("top_k"))
        return self.search(query=query, top_k=top_k)

    def search(self, *, query: str, top_k: int | None = None) -> ToolCallResult:
        query = (query or "").strip()
        if not query:
            return ToolCallResult(
                tool_name=self.name,
                success=False,
                error="query_required",
            )
        if not self.enabled:
            return ToolCallResult(
                tool_name=self.name,
                success=False,
                content="Web Search tool is disabled. Configure WEB_SEARCH_ENABLED=true and a provider adapter to enable it.",
                raw={"provider": self.provider, "query": query, "top_k": top_k or self.default_top_k},
                error="tool_not_configured",
            )
        if self.provider == "mcp":
            return self._search_with_mcp(query=query, top_k=top_k or self.default_top_k)
        return ToolCallResult(
            tool_name=self.name,
            success=False,
            raw={"provider": self.provider, "query": query, "top_k": top_k or self.default_top_k},
            error=f"unsupported_web_search_provider:{self.provider}",
        )

    def _search_with_mcp(self, *, query: str, top_k: int) -> ToolCallResult:
        # MCP client wiring will be added when a concrete web-search MCP server
        # is selected. Keeping this boundary explicit avoids leaking provider
        # details into the RAG workflow.
        return ToolCallResult(
            tool_name=self.name,
            success=False,
            content="MCP web search adapter is not wired yet.",
            raw={"provider": "mcp", "query": query, "top_k": top_k},
            error="mcp_adapter_not_wired",
        )

    def _coerce_top_k(self, value: Any) -> int:
        try:
            top_k = int(value)
        except (TypeError, ValueError):
            top_k = self.default_top_k
        return max(1, min(top_k, 10))
