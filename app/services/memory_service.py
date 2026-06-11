from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import redis
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.llms import ChatMessage, MessageRole
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.llm import LLMTask, get_llm_gateway
from app.auth.repository import (
    create_chat_exchange,
    get_session_factory,
    get_session_summary,
    list_chat_messages,
    list_recent_chat_messages,
    upsert_session_summary,
)


DEFAULT_MEMORY_CONTEXT_TURNS = 3
SUMMARY_TRIGGER_MESSAGES = 12
SUMMARY_RECENT_MESSAGES = DEFAULT_MEMORY_CONTEXT_TURNS * 2
SUMMARY_MAX_INPUT_CHARS = 6000
SUMMARY_MAX_MESSAGE_CHARS = 1200
FALLBACK_SUMMARY_MAX_CHARS = 500


@dataclass
class MemoryContext:
    """Prompt-ready session memory exposed to downstream LLM nodes."""

    session_id: str
    recent_messages: list[dict] = field(default_factory=list)
    session_summary: str = ""
    long_term_memories: list[str] = field(default_factory=list)
    message_count: int = 0
    used_summary: bool = False

    @property
    def has_context(self) -> bool:
        return bool(self.long_term_memories or self.session_summary or self.recent_messages)


class MemoryService:
    """Session-scoped memory facade for the chat API.

    Redis is treated as a cache/persistence layer for now, with an in-process
    fallback so local development can continue when Redis is unavailable.
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.redis = redis_client
        self.memory_buffers: dict[str, ChatMemoryBuffer] = {}
        self._metadata_buffers: dict[str, list[dict]] = {}
        self._compression_locks: dict[str, threading.Lock] = {}

    @staticmethod
    def sessions_key() -> str:
        return "rag:chat:sessions"

    @staticmethod
    def memory_key(session_id: str) -> str:
        return f"rag:chat:memory:{session_id}"

    @staticmethod
    def metadata_key(session_id: str) -> str:
        return f"rag:chat:metadata:{session_id}"

    def load(self, session_id: str) -> ChatMemoryBuffer:
        memory = self._load_from_redis(session_id)
        if memory is not None:
            self.memory_buffers[session_id] = memory
            return memory

        memory = self.memory_buffers.get(session_id)
        if memory is None:
            memory = ChatMemoryBuffer.from_defaults(token_limit=4096)
            self.memory_buffers[session_id] = memory
        return memory

    def save(self, session_id: str, memory: ChatMemoryBuffer) -> None:
        self.memory_buffers[session_id] = memory
        if self.redis is None:
            return
        try:
            payload = json.dumps(memory.to_dict(), ensure_ascii=False)
            self.redis.setex(self.memory_key(session_id), app_settings.chat_msg_ttl, payload)
            self.touch(session_id)
        except Exception:
            return

    def touch(self, session_id: str) -> None:
        if self.redis is None:
            return
        try:
            self.redis.zadd(self.sessions_key(), {session_id: time.time()})
            self.redis.expire(self.sessions_key(), app_settings.session_list_ttl)
        except Exception:
            return

    def delete(self, session_id: str) -> None:
        self.memory_buffers.pop(session_id, None)
        self._metadata_buffers.pop(session_id, None)
        if self.redis is None:
            return
        try:
            self.redis.zrem(self.sessions_key(), session_id)
            self.redis.delete(self.memory_key(session_id))
            self.redis.delete(self.metadata_key(session_id))
        except Exception:
            return

    def list_sessions(self, limit: int = 50) -> list[str]:
        if self.redis is None:
            return []
        try:
            return self.redis.zrevrange(self.sessions_key(), 0, max(0, limit - 1))
        except Exception:
            return []

    def _load_metadata_from_redis(self, session_id: str) -> list[dict]:
        if self.redis is None:
            return self._metadata_buffers.get(session_id, [])
        try:
            raw = self.redis.get(self.metadata_key(session_id))
            if not raw:
                return []
            return json.loads(raw)
        except Exception:
            return []

    def _save_metadata_to_redis(self, session_id: str, metadata: list[dict]) -> None:
        self._metadata_buffers[session_id] = metadata
        if self.redis is None:
            return
        try:
            payload = json.dumps(metadata, ensure_ascii=False)
            self.redis.setex(self.metadata_key(session_id), app_settings.chat_msg_ttl, payload)
        except Exception:
            return

    def messages(self, session_id: str) -> list[dict]:
        memory = self._load_from_redis(session_id)
        if memory is None:
            memory = self.memory_buffers.get(session_id)
        if memory is None:
            return []

        metadata_list = self._load_metadata_from_redis(session_id)
        messages = []
        for i, message in enumerate(memory.get()):
            role = getattr(getattr(message, "role", None), "value", None) or str(message.role)
            msg_dict = {"role": role, "content": message.content}
            if i < len(metadata_list):
                msg_dict["sources"] = metadata_list[i].get("sources", [])
                msg_dict["citations"] = metadata_list[i].get("citations", [])
                msg_dict["route"] = metadata_list[i].get("route")
                msg_dict["trace"] = metadata_list[i].get("trace")
            messages.append(msg_dict)
        return messages

    def build_context(
        self,
        session_id: str,
        memory: ChatMemoryBuffer,
        *,
        max_turns: int = DEFAULT_MEMORY_CONTEXT_TURNS,
        use_recent_messages: bool = True,
        use_session_summary: bool = True,
        db: Session | None = None,
    ) -> MemoryContext:
        """Build prompt-ready recent session context without trace metadata."""

        all_messages = []
        if use_recent_messages:
            try:
                all_messages = list(memory.get())
            except Exception:
                all_messages = []

        recent_messages = (
            self._to_context_messages(all_messages[-max_turns * 2 :])
            if use_recent_messages
            else []
        )

        summary = ""
        if db is not None:
            try:
                if use_recent_messages and not recent_messages:
                    recent_rows = list_recent_chat_messages(db, session_id, max_turns * 2)
                    recent_messages = self._to_context_messages(recent_rows)
                    if recent_rows:
                        self._trim_memory_to_recent(memory, recent_rows)
                        self.save(session_id, memory)
                if use_session_summary:
                    summary_row = get_session_summary(db, session_id)
                    summary = summary_row.summary if summary_row is not None else ""
            except Exception:
                summary = ""

        return MemoryContext(
            session_id=session_id,
            recent_messages=recent_messages,
            session_summary=summary,
            message_count=len(all_messages),
            used_summary=bool(summary),
        )

    def debug_snapshot(self, session_id: str, *, db: Session) -> dict:
        """Return a development-only view of memory storage layers."""

        persisted_messages = list_chat_messages(db, session_id)
        summary_row = get_session_summary(db, session_id)
        recent_rows = list_recent_chat_messages(db, session_id, SUMMARY_RECENT_MESSAGES)
        memory = self.load(session_id)
        context = self.build_context(session_id, memory, db=db)

        try:
            redis_messages = list(memory.get())
        except Exception:
            redis_messages = []

        return {
            "session_id": session_id,
            "thresholds": {
                "summary_trigger_messages": SUMMARY_TRIGGER_MESSAGES,
                "recent_messages": SUMMARY_RECENT_MESSAGES,
                "recent_turns": DEFAULT_MEMORY_CONTEXT_TURNS,
            },
            "postgres": {
                "message_count": len(persisted_messages),
                "recent_message_count": len(recent_rows),
                "last_message_id": persisted_messages[-1].id if persisted_messages else None,
                "recent_messages": self._to_context_messages(recent_rows),
            },
            "summary": {
                "exists": summary_row is not None,
                "covered_until_message_id": (
                    summary_row.covered_until_message_id if summary_row is not None else None
                ),
                "covered_message_count": (
                    summary_row.covered_message_count if summary_row is not None else 0
                ),
                "version": summary_row.version if summary_row is not None else None,
                "text": summary_row.summary if summary_row is not None else "",
            },
            "redis": {
                "enabled": self.redis is not None,
                "memory_message_count": len(redis_messages),
                "memory_messages": self._to_context_messages(redis_messages),
                "metadata_count": len(self._load_metadata_from_redis(session_id)),
            },
            "prompt_context": {
                "used_summary": context.used_summary,
                "has_context": context.has_context,
                "recent_messages": context.recent_messages,
                "session_summary": context.session_summary,
            },
        }

    @staticmethod
    def _to_context_messages(messages: list) -> list[dict]:
        recent_messages: list[dict] = []
        for message in messages:
            role = getattr(getattr(message, "role", None), "value", None) or str(message.role)
            content = str(getattr(message, "content", "") or "").strip()
            if content:
                recent_messages.append({"role": role, "content": content})
        return recent_messages

    def append_user_message(self, memory: ChatMemoryBuffer, content: str) -> None:
        self._append(memory, MessageRole.USER, content)

    def append_assistant_message(self, memory: ChatMemoryBuffer, content: str) -> None:
        self._append(memory, MessageRole.ASSISTANT, content)

    def append_exchange(
        self,
        memory: ChatMemoryBuffer,
        user_content: str,
        assistant_content: str,
        *,
        assistant_status: str = "completed",
    ) -> None:
        self.append_user_message(memory, user_content)
        assistant_content = (assistant_content or "").strip()
        if assistant_status != "completed":
            assistant_content = f"[{assistant_status}] {assistant_content}"
        self.append_assistant_message(memory, assistant_content)

    def append_exchange_with_metadata(
        self,
        session_id: str,
        memory: ChatMemoryBuffer,
        user_content: str,
        assistant_content: str,
        *,
        sources: list = None,
        citations: list = None,
        route: dict = None,
        trace: dict = None,
        assistant_status: str = "completed",
        db: Session | None = None,
        owner_user_id: str = "",
    ) -> None:
        self.append_exchange(memory, user_content, assistant_content, assistant_status=assistant_status)
        metadata = self._load_metadata_from_redis(session_id)
        user_metadata = {}
        assistant_metadata = {
            "sources": sources or [],
            "citations": citations or [],
            "route": route,
            "trace": trace,
        }
        metadata.append(user_metadata)
        metadata.append(assistant_metadata)
        metadata = self._sync_metadata_to_memory(memory, metadata)
        self._save_metadata_to_redis(session_id, metadata)
        if db is not None and owner_user_id:
            try:
                create_chat_exchange(
                    db,
                    session_id=session_id,
                    owner_user_id=owner_user_id,
                    user_content=user_content,
                    assistant_content=(assistant_content or "").strip(),
                    assistant_status=assistant_status,
                    assistant_metadata=assistant_metadata,
                )
            except Exception:
                db.rollback()

    def compress_session_in_background(self, session_id: str, owner_user_id: str) -> None:
        """Run rolling-summary compression outside the request DB session."""

        lock = self._compression_locks.setdefault(session_id, threading.Lock())
        if not lock.acquire(blocking=False):
            return

        db = None
        try:
            db = get_session_factory()()
            memory = self.load(session_id)
            if self.maybe_compress(session_id, memory, db=db, owner_user_id=owner_user_id):
                self.save(session_id, memory)
        except Exception as exc:
            if db is not None:
                db.rollback()
            print(f"[Memory] background summary compression skipped: {exc}")
        finally:
            if db is not None:
                db.close()
            lock.release()

    def _load_from_redis(self, session_id: str) -> Optional[ChatMemoryBuffer]:
        if self.redis is None:
            return None
        try:
            raw = self.redis.get(self.memory_key(session_id))
            if not raw:
                return None
            return ChatMemoryBuffer.from_dict(json.loads(raw))
        except Exception:
            return None

    def maybe_compress(
        self,
        session_id: str,
        memory: ChatMemoryBuffer,
        *,
        db: Session,
        owner_user_id: str,
    ) -> bool:
        """Roll old persisted exchanges into session summary and trim buffer."""

        persisted = list_chat_messages(db, session_id)
        if len(persisted) <= SUMMARY_TRIGGER_MESSAGES:
            return False

        recent = persisted[-SUMMARY_RECENT_MESSAGES:]
        evicted = persisted[:-SUMMARY_RECENT_MESSAGES]
        summary_row = get_session_summary(db, session_id)
        covered_id = summary_row.covered_until_message_id if summary_row is not None else None
        new_messages = [message for message in evicted if covered_id is None or message.id > covered_id]
        if not new_messages:
            self._trim_memory_to_recent(memory, recent)
            self._trim_metadata_to_recent(session_id, len(recent))
            return False

        summary = self._summarize(
            previous_summary=summary_row.summary if summary_row is not None else "",
            messages=new_messages,
        )
        if not summary:
            return False

        upsert_session_summary(
            db,
            session_id=session_id,
            owner_user_id=owner_user_id,
            summary=summary,
            covered_until_message_id=new_messages[-1].id,
            covered_message_count=(summary_row.covered_message_count if summary_row is not None else 0)
            + len(new_messages),
        )
        self._trim_memory_to_recent(memory, recent)
        self._trim_metadata_to_recent(session_id, len(recent))
        return True

    def compression_plan(self, session_id: str, *, db: Session) -> dict:
        """Inspect why a session would or would not be compressed."""

        persisted = list_chat_messages(db, session_id)
        summary_row = get_session_summary(db, session_id)
        covered_id = summary_row.covered_until_message_id if summary_row is not None else None
        recent = persisted[-SUMMARY_RECENT_MESSAGES:] if persisted else []
        evicted = persisted[:-SUMMARY_RECENT_MESSAGES] if persisted else []
        new_messages = [
            message for message in evicted if covered_id is None or message.id > covered_id
        ]
        return {
            "message_count": len(persisted),
            "trigger_messages": SUMMARY_TRIGGER_MESSAGES,
            "recent_message_count": len(recent),
            "evicted_message_count": len(evicted),
            "new_message_count": len(new_messages),
            "covered_until_message_id": covered_id,
            "should_compress": len(persisted) > SUMMARY_TRIGGER_MESSAGES and bool(new_messages),
            "new_message_ids": [message.id for message in new_messages],
        }

    @staticmethod
    def _trim_memory_to_recent(memory: ChatMemoryBuffer, recent_messages: list) -> None:
        if not hasattr(memory, "reset"):
            return
        memory.reset()
        for message in recent_messages:
            role = MessageRole.USER if message.role == "user" else MessageRole.ASSISTANT
            content = (message.content or "").strip()
            if message.role == "assistant" and message.status != "completed":
                content = f"[{message.status}] {content}"
            if content:
                memory.put(ChatMessage(role=role, content=content))

    def _trim_metadata_to_recent(self, session_id: str, recent_message_count: int) -> None:
        metadata = self._load_metadata_from_redis(session_id)
        if not metadata:
            return
        if recent_message_count <= 0:
            self._save_metadata_to_redis(session_id, [])
            return
        self._save_metadata_to_redis(session_id, metadata[-recent_message_count:])

    @staticmethod
    def _sync_metadata_to_memory(memory: ChatMemoryBuffer, metadata: list[dict]) -> list[dict]:
        try:
            message_count = len(memory.get())
        except Exception:
            return metadata
        if message_count <= 0:
            return []
        return metadata[-message_count:]

    @staticmethod
    def _summarize(previous_summary: str, messages: list) -> str:
        messages_text = MemoryService._format_messages_for_summary(messages)
        if not messages_text:
            return previous_summary

        prompt = f"""请为企业知识库问答会话更新滚动摘要。

已有摘要：
{previous_summary or "无"}

本次被移出短期窗口的有效对话：
{messages_text}

要求：
1. 重点保留用户目标、项目事实、已确认结论、约束、未完成问题。
2. 不要写 RAG trace、检索 chunk、HyDE、Step-back 或内部策略细节。
3. 不要记录“无法回答”“知识库无关”“请求超时”等低价值失败内容。
4. 不要新增对话中没有的事实。
5. 使用简洁中文，不超过 260 字。

更新后的摘要："""
        response = get_llm_gateway().chat_completion(
            task=LLMTask.MEMORY_SUMMARY,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=320,
        )
        summary = (response.choices[0].message.content or "").strip()
        if summary:
            return summary
        return MemoryService._fallback_summary(previous_summary, messages)

    @staticmethod
    def _fallback_summary(previous_summary: str, messages: list) -> str:
        lines = []
        if previous_summary:
            lines.append(previous_summary.strip())
        clean_lines = list(lines)
        for message in messages:
            content = str(message.content or "").strip()
            if not content or MemoryService._is_low_value_summary_message(message):
                continue
            if message.role == "user":
                clean_lines.append(f"用户曾提到：{content[:120]}")
            elif message.role == "assistant" and message.status == "completed":
                clean_lines.append(f"助手曾确认：{content[:120]}")
            if len(clean_lines) >= 5:
                break
        return "；".join(clean_lines)[:FALLBACK_SUMMARY_MAX_CHARS]

    @staticmethod
    def _format_messages_for_summary(messages: list) -> str:
        lines = []
        used_chars = 0
        for message in messages:
            if MemoryService._is_low_value_summary_message(message):
                continue
            content = str(message.content or "").strip()
            if content:
                clipped = content[:SUMMARY_MAX_MESSAGE_CHARS]
                line = f"{message.role}: {clipped}"
                if used_chars + len(line) > SUMMARY_MAX_INPUT_CHARS:
                    remaining = SUMMARY_MAX_INPUT_CHARS - used_chars
                    if remaining <= 0:
                        break
                    line = line[:remaining]
                lines.append(line)
                used_chars += len(line)
                if used_chars >= SUMMARY_MAX_INPUT_CHARS:
                    break
        return "\n".join(lines)

    @staticmethod
    def _is_low_value_summary_message(message) -> bool:
        content = str(getattr(message, "content", "") or "").strip()
        if not content:
            return True
        if getattr(message, "status", "completed") != "completed":
            return True
        if getattr(message, "role", "") != "assistant":
            return False

        low_value_markers = (
            "[error]",
            "Answer generation failed",
            "Request timed out",
            "无法回答",
            "知识库内容不相关",
            "完全不相关",
            "缺少相关信息",
            "没有任何关于",
            "没有任何",
        )
        return any(marker in content for marker in low_value_markers)

    @staticmethod
    def _append(memory: ChatMemoryBuffer, role: MessageRole, content: str) -> None:
        content = (content or "").strip()
        if not content:
            return
        memory.put(ChatMessage(role=role, content=content))
