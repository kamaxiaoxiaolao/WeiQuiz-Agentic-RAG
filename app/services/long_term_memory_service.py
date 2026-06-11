from __future__ import annotations

from typing import Any

from app.config import settings as app_settings


MAX_LONG_TERM_MESSAGE_CHARS = 2000

EXPLICIT_MEMORY_MARKERS = (
    "记住",
    "请记住",
    "帮我记住",
    "remember",
)

DURABLE_MEMORY_MARKERS = (
    "我的目标",
    "我的偏好",
    "我喜欢",
    "我不喜欢",
    "我习惯",
    "我希望",
    "我正在",
    "我当前",
    "以后",
    "以后都",
    "下次",
    "下次请",
    "我的项目",
    "项目是",
    "系统是",
    "架构是",
    "技术栈",
    "当前记忆系统",
)

LOW_VALUE_MEMORY_MARKERS = (
    "[error]",
    "Answer generation failed",
    "Request timed out",
    "无法回答",
    "知识库内容不相关",
    "完全不相关",
    "缺少相关信息",
    "没有任何关于",
)


class LongTermMemoryService:
    """Optional Mem0 adapter for cross-session semantic memory.

    The service is intentionally best-effort: Mem0 failures must not break the
    core RAG chat flow.
    """

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        api_key: str | None = None,
        mode: str | None = None,
        search_limit: int | None = None,
    ) -> None:
        self.enabled = app_settings.mem0_enabled if enabled is None else enabled
        self.api_key = app_settings.mem0_api_key if api_key is None else api_key
        self.mode = (app_settings.mem0_mode if mode is None else mode).lower()
        self.search_limit = app_settings.mem0_search_limit if search_limit is None else search_limit
        self._client: Any | None = None

    def is_enabled(self) -> bool:
        return bool(self.enabled and (self.api_key or self.mode == "local"))

    @classmethod
    def should_write(cls, user_message: str, assistant_answer: str) -> bool:
        """Return whether an exchange is durable enough for long-term memory.

        Mainstream memory systems avoid writing every turn. They keep explicit
        preferences, goals, stable project facts, and user instructions, while
        dropping transient Q&A failures and one-off retrieval noise.
        """

        user_text = (user_message or "").strip()
        assistant_text = (assistant_answer or "").strip()
        if not user_text or not assistant_text:
            return False
        if cls._is_low_value_content(assistant_text):
            return False
        return any(
            marker in user_text
            for marker in EXPLICIT_MEMORY_MARKERS + DURABLE_MEMORY_MARKERS
        )

    def search(self, user_id: str, query: str, limit: int | None = None) -> list[str]:
        if not self.is_enabled() or not user_id or not query.strip():
            return []

        client = self._get_client()
        if client is None:
            return []

        try:
            raw_results = client.search(
                query=query,
                user_id=user_id,
                limit=limit or self.search_limit,
            )
        except TypeError:
            raw_results = client.search(
                query=query,
                filters={"user_id": user_id},
                limit=limit or self.search_limit,
            )
        except Exception:
            return []

        return self._normalize_search_results(raw_results)

    def add(self, user_id: str, messages: list[dict]) -> None:
        if not self.is_enabled() or not user_id or not messages:
            return

        client = self._get_client()
        if client is None:
            return

        clean_messages = self._filter_messages(messages)
        if not clean_messages:
            return

        try:
            client.add(messages=clean_messages, user_id=user_id)
        except TypeError:
            client.add(clean_messages, user_id=user_id)
        except Exception:
            return

    def _get_client(self):
        if self._client is not None:
            return self._client

        try:
            if self.mode == "local":
                from mem0 import Memory

                self._client = Memory.from_config({})
            else:
                from mem0 import MemoryClient

                self._client = MemoryClient(api_key=self.api_key)
        except Exception:
            self._client = None
        return self._client

    @staticmethod
    def _filter_messages(messages: list[dict]) -> list[dict]:
        clean_messages = []
        for message in messages:
            role = str(message.get("role", "")).strip()
            content = str(message.get("content", "")).strip()
            if role not in {"user", "assistant", "system"} or not content:
                continue
            if LongTermMemoryService._is_low_value_content(content):
                continue
            content = content[:MAX_LONG_TERM_MESSAGE_CHARS]
            clean_messages.append({"role": role, "content": content})
        return clean_messages

    @staticmethod
    def _is_low_value_content(content: str) -> bool:
        return any(marker in content for marker in LOW_VALUE_MEMORY_MARKERS)

    @staticmethod
    def _normalize_search_results(raw_results) -> list[str]:
        if not raw_results:
            return []

        if isinstance(raw_results, dict):
            raw_results = raw_results.get("results") or raw_results.get("memories") or []

        memories: list[str] = []
        seen: set[str] = set()
        for item in raw_results:
            if isinstance(item, str):
                memory = item.strip()
            elif isinstance(item, dict):
                memory = str(
                    item.get("memory")
                    or item.get("text")
                    or item.get("content")
                    or ""
                ).strip()
            else:
                memory = str(getattr(item, "memory", "") or getattr(item, "text", "") or "").strip()
            if memory and memory not in seen:
                seen.add(memory)
                memories.append(memory)
        return memories
