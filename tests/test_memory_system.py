from llama_index.core.memory import ChatMemoryBuffer

import app.services.memory_service as memory_module
from app.services.long_term_memory_service import (
    MAX_LONG_TERM_MESSAGE_CHARS,
    LongTermMemoryService,
)
from app.services.memory_service import SUMMARY_MAX_INPUT_CHARS, MemoryService


class MessageRow:
    def __init__(self, role: str, content: str, status: str = "completed", id: int = 1):
        self.role = role
        self.content = content
        self.status = status
        self.id = id


def test_build_context_respects_recent_message_policy():
    service = MemoryService()
    memory = ChatMemoryBuffer.from_defaults(token_limit=4096)
    service.append_user_message(memory, "请记住我的项目是企业知识库问答")
    service.append_assistant_message(memory, "好的，我会在本会话中参考这个背景。")

    context = service.build_context(
        "session-a",
        memory,
        use_recent_messages=False,
        use_session_summary=False,
    )

    assert context.recent_messages == []
    assert context.session_summary == ""
    assert not context.has_context


def test_build_context_hydrates_short_term_window_from_persistent_history(monkeypatch):
    service = MemoryService()
    memory = ChatMemoryBuffer.from_defaults(token_limit=4096)
    rows = [
        MessageRow("user", "我的项目是 WeiQuiz", id=1),
        MessageRow("assistant", "明白，我会参考这个项目背景。", id=2),
    ]

    monkeypatch.setattr(memory_module, "list_recent_chat_messages", lambda db, sid, limit: rows)
    monkeypatch.setattr(memory_module, "get_session_summary", lambda db, sid: None)

    context = service.build_context("session-a", memory, db=object())

    assert context.recent_messages == [
        {"role": "user", "content": "我的项目是 WeiQuiz"},
        {"role": "assistant", "content": "明白，我会参考这个项目背景。"},
    ]
    assert [message.content for message in memory.get()] == [
        "我的项目是 WeiQuiz",
        "明白，我会参考这个项目背景。",
    ]


def test_metadata_is_trimmed_to_current_short_term_window():
    service = MemoryService()
    memory = ChatMemoryBuffer.from_defaults(token_limit=4096)
    for i in range(3):
        service.append_user_message(memory, f"user-{i}")
        service.append_assistant_message(memory, f"assistant-{i}")
    service._save_metadata_to_redis(
        "session-a",
        [{"i": i} for i in range(6)],
    )

    service._trim_memory_to_recent(
        memory,
        [
            MessageRow("user", "user-2", id=5),
            MessageRow("assistant", "assistant-2", id=6),
        ],
    )
    service._trim_metadata_to_recent("session-a", len(memory.get()))

    assert service._load_metadata_from_redis("session-a") == [{"i": 4}, {"i": 5}]


def test_summary_input_has_total_budget():
    rows = [
        MessageRow("user", "a" * 2000, id=i)
        for i in range(10)
    ]

    summary_input = MemoryService._format_messages_for_summary(rows)

    assert len(summary_input) <= SUMMARY_MAX_INPUT_CHARS + len(rows)


def test_long_term_memory_write_gate_keeps_durable_user_facts():
    assert LongTermMemoryService.should_write(
        "请记住，我的偏好是回答时先给结论再解释。",
        "已记住：后续回答会优先先给结论。",
    )
    assert LongTermMemoryService.should_write(
        "我的项目是 WeiQuiz，技术栈包含 FastAPI、Redis 和 PostgreSQL。",
        "明白，我会把这个项目背景作为长期上下文。",
    )


def test_long_term_memory_write_gate_drops_transient_or_failed_turns():
    assert not LongTermMemoryService.should_write("今天几点了？", "现在是下午三点。")
    assert not LongTermMemoryService.should_write(
        "请记住我的目标是完善记忆系统。",
        "无法回答，知识库内容不相关。",
    )


def test_long_term_memory_filters_truncates_and_deduplicates():
    long_text = "a" * (MAX_LONG_TERM_MESSAGE_CHARS + 20)
    messages = LongTermMemoryService._filter_messages(
        [
            {"role": "user", "content": long_text},
            {"role": "assistant", "content": "无法回答"},
            {"role": "tool", "content": "ignored"},
        ]
    )

    assert len(messages) == 1
    assert len(messages[0]["content"]) == MAX_LONG_TERM_MESSAGE_CHARS

    memories = LongTermMemoryService._normalize_search_results(
        {
            "results": [
                {"memory": "用户偏好先给结论"},
                {"text": "用户偏好先给结论"},
                {"content": "项目是 WeiQuiz"},
            ]
        }
    )

    assert memories == ["用户偏好先给结论", "项目是 WeiQuiz"]
