# app/agentic/memory.py
"""Memory System for Agentic RAG.

实现：
1. SessionMemoryManager - 管理单个会话的消息和摘要压缩
2. MemoryStore - 管理多个会话的存储（Redis + 内存 fallback）
3. MemoryStats, MemoryContext - 数据模型
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.schema import ChatMessage, MessageRole

from app.config import settings
from app.llm import LLMTask, get_llm_gateway

logger = logging.getLogger(__name__)

# ============================================================
# 1. 配置常量
# ============================================================

DEFAULT_TOKEN_LIMIT = 4096
DEFAULT_RECENT_TURNS = 4
SUMMARY_TRIGGER_TOKENS = 3000  # 超过此阈值触发摘要压缩


# ============================================================
# 2. 数据模型
# ============================================================

@dataclass
class MemoryStats:
    """记忆状态统计信息（用于可观测性）"""
    message_count: int = 0
    total_tokens: int = 0
    summary_used: bool = False
    summary_updated: bool = False
    long_term_memories_used: int = 0
    compression_reason: str = ""


@dataclass
class MemoryContext:
    """组装好的记忆上下文，用于注入 Prompt"""
    recent_messages: str = ""
    session_summary: str = ""
    long_term_memories: str = ""
    stats: MemoryStats = field(default_factory=MemoryStats)


# ============================================================
# 3. SessionMemoryManager 实现
# ============================================================

class SessionMemoryManager:
    """管理单个会话的短期记忆，包括消息管理和摘要压缩"""

    def __init__(self, session_id: str, token_limit: int = DEFAULT_TOKEN_LIMIT):
        self.session_id = session_id
        self._memory = ChatMemoryBuffer.from_defaults(token_limit=token_limit)
        self._summary: str = ""
        self._stats = MemoryStats()

    @property
    def messages(self) -> List[ChatMessage]:
        """获取当前会话的所有消息"""
        return list(self._memory.get())

    @property
    def summary(self) -> str:
        """获取会话摘要"""
        return self._summary

    @property
    def stats(self) -> MemoryStats:
        """获取统计信息"""
        return self._stats

    def add_message(self, role: str, content: str) -> None:
        """添加一条消息到记忆中"""
        role_enum = MessageRole(role.lower())
        self._memory.put(ChatMessage(role=role_enum, content=content))
        self._stats.message_count += 1

    def maybe_compress(self) -> bool:
        """检查是否需要压缩，并执行滚动摘要压缩

        当历史消息 token 数超过 SUMMARY_TRIGGER_TOKENS 时，
        将早期消息压缩为摘要，保留最近几轮完整消息。
        """
        tokens = self._estimate_tokens()
        if tokens < SUMMARY_TRIGGER_TOKENS:
            self._stats.compression_reason = "threshold_not_reached"
            return False

        self._stats.compression_reason = f"token_limit_exceeded({tokens}>{SUMMARY_TRIGGER_TOKENS})"
        return self._compress_to_summary()

    def _estimate_tokens(self) -> int:
        """估算当前消息的 token 数（简单估算：1 token ≈ 4 字符）"""
        total_chars = sum(len(str(msg.content)) for msg in self._memory.get())
        return total_chars // 4

    def _compress_to_summary(self) -> bool:
        """执行滚动摘要压缩：将早期消息合并为摘要，保留最近消息"""
        try:
            messages = self._memory.get()
            if len(messages) <= DEFAULT_RECENT_TURNS:
                return False

            # 分离旧消息和最近消息
            old_messages = messages[:-DEFAULT_RECENT_TURNS]
            recent_messages = messages[-DEFAULT_RECENT_TURNS:]

            # 生成新摘要
            new_summary = self._generate_summary(old_messages)

            # 更新状态
            self._summary = new_summary
            self._stats.summary_updated = True
            self._stats.summary_used = True

            # 重建 memory，保留最近消息
            self._memory = ChatMemoryBuffer.from_defaults(token_limit=self._memory.token_limit)
            for msg in recent_messages:
                self._memory.put(msg)

            logger.info(
                f"[Memory] Session {self.session_id} compressed: "
                f"{len(old_messages)} messages -> summary, "
                f"{len(recent_messages)} recent messages kept"
            )
            return True
        except Exception as e:
            logger.error(f"[Memory] Compression failed for {self.session_id}: {e}")
            self._stats.compression_reason = f"compression_error:{str(e)}"
            return False

    def _generate_summary(self, messages: List[ChatMessage]) -> str:
        """使用 LLM 生成会话摘要"""
        messages_text = "\n".join([
            f"{msg.role.value}: {msg.content}"
            for msg in messages
        ])

        prompt = f"""请总结以下对话历史，提取关键信息：

对话历史：
{messages_text}

总结要求：
1. 保留用户的核心目标和需求
2. 记录已确认的事实和决策
3. 列出未完成的任务或待确认事项
4. 不要包含大段原始文本
5. 使用简洁的中文，不超过200字

总结："""

        response = get_llm_gateway().chat_completion(
            task=LLMTask.MEMORY_SUMMARY,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )

        return response.choices[0].message.content.strip()

    def build_context(self) -> MemoryContext:
        """构建用于 Prompt 的记忆上下文"""
        # 构建最近消息
        recent_messages = []
        for msg in self._memory.get()[-DEFAULT_RECENT_TURNS:]:
            role = msg.role.value if hasattr(msg.role, 'value') else str(msg.role)
            recent_messages.append(f"{role}: {msg.content}")

        self._stats.total_tokens = self._estimate_tokens()

        return MemoryContext(
            recent_messages="\n".join(recent_messages),
            session_summary=self._summary,
            long_term_memories="",  # 长期记忆后续实现
            stats=self._stats,
        )

    def to_dict(self) -> Dict[str, Any]:
        """序列化记忆状态（用于持久化到 Redis）"""
        return {
            "session_id": self.session_id,
            "memory": self._memory.to_dict(),
            "summary": self._summary,
            "stats": {
                "message_count": self._stats.message_count,
                "summary_updated": self._stats.summary_updated,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionMemoryManager':
        """从序列化数据恢复记忆"""
        manager = cls(data["session_id"])
        manager._memory = ChatMemoryBuffer.from_dict(data["memory"])
        manager._summary = data.get("summary", "")
        if "stats" in data:
            manager._stats.message_count = data["stats"].get("message_count", 0)
            manager._stats.summary_updated = data["stats"].get("summary_updated", False)
        return manager


# ============================================================
# 4. MemoryStore 实现
# ============================================================

class MemoryStore:
    """全局记忆存储管理器：适配 Redis + 内存 fallback"""

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._memory_buffers: Dict[str, SessionMemoryManager] = {}

    def _memory_key(self, session_id: str) -> str:
        return f"rag:chat:memory:{session_id}"

    def get(self, session_id: str) -> SessionMemoryManager:
        """获取或创建会话记忆

        查找顺序：
        1. 内存缓存
        2. Redis
        3. 创建新的
        """
        # 先从内存缓存获取
        if session_id in self._memory_buffers:
            logger.debug(f"[MemoryStore] HIT memory cache: {session_id}")
            return self._memory_buffers[session_id]

        # 再从 Redis 获取
        if self._redis is not None:
            try:
                raw = self._redis.get(self._memory_key(session_id))
                if raw:
                    data = json.loads(raw)
                    manager = SessionMemoryManager.from_dict(data)
                    self._memory_buffers[session_id] = manager
                    logger.info(f"[MemoryStore] HIT redis: {session_id}, messages={len(manager.messages)}")
                    return manager
            except Exception as e:
                logger.warning(f"[MemoryStore] Redis read failed for {session_id}: {e}")

        # 创建新的记忆管理器
        manager = SessionMemoryManager(session_id)
        self._memory_buffers[session_id] = manager
        logger.info(f"[MemoryStore] CREATE new memory: {session_id}")
        return manager

    def save(self, session_id: str, manager: SessionMemoryManager) -> bool:
        """保存会话记忆到 Redis"""
        if self._redis is not None:
            try:
                payload = json.dumps(manager.to_dict(), ensure_ascii=False)
                self._redis.setex(
                    self._memory_key(session_id),
                    settings.chat_msg_ttl,
                    payload
                )
                logger.info(f"[MemoryStore] SAVED to redis: {session_id}")
                return True
            except Exception as e:
                logger.warning(f"[MemoryStore] Redis write failed for {session_id}: {e}")
                return False
        return False

    def delete(self, session_id: str) -> None:
        """删除会话记忆"""
        if session_id in self._memory_buffers:
            del self._memory_buffers[session_id]

        if self._redis is not None:
            try:
                self._redis.delete(self._memory_key(session_id))
                logger.info(f"[MemoryStore] DELETED: {session_id}")
            except Exception as e:
                logger.warning(f"[MemoryStore] Redis delete failed for {session_id}: {e}")
