"""Agentic RAG Workflow 测试用例

运行: pytest tests/test_rag_workflow.py -v
注意: 使用 mock 检索函数，不依赖 Milvus/PostgreSQL
"""

import pytest
from dataclasses import dataclass
from typing import Optional, Callable

from app.agentic.rag_workflow import (
    WorkflowResult,
    run_agentic_rag,
    set_retrieve_fn,
    generate_answer,
)
from app.agentic.retrieval_quality import QualityThresholds


# ============================================================
# Mock NodeWithScore
# ============================================================

@dataclass
class MockNode:
    text: str = ""
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class MockNodeWithScore:
    node: MockNode
    score: Optional[float] = None

    @property
    def text(self):
        return self.node.text

    @property
    def metadata(self):
        return self.node.metadata


def make_node(text: str, score: float, **metadata) -> MockNodeWithScore:
    return MockNodeWithScore(
        node=MockNode(text=text, metadata=metadata),
        score=score,
    )


# ============================================================
# Mock 检索函数
# ============================================================

def mock_retrieve_good(query: str, top_k: int = 5) -> list:
    """模拟高质量检索结果。"""
    return [
        make_node("公司差旅报销标准：住宿每晚不超过500元，交通凭票报销。" * 3, 0.85,
                  source_path="报销制度.pdf"),
        make_node("差旅费用包括住宿、交通、餐饮，需提前申请审批。" * 3, 0.72,
                  source_path="报销制度.pdf"),
    ]


def mock_retrieve_bad(query: str, top_k: int = 5) -> list:
    """模拟低质量检索结果。"""
    return [
        make_node("这是一段不太相关的文本。", 0.15, source_path="其他文档.pdf"),
    ]


def mock_retrieve_empty(query: str, top_k: int = 5) -> list:
    """模拟空检索结果。"""
    return []


def mock_retrieve_dynamic(query: str, top_k: int = 5) -> list:
    """模拟动态检索：第一轮差，第二轮好（基于 top_k）。"""
    if top_k <= 5:
        # 第一轮：低质量
        return [make_node("不太相关", 0.2)]
    else:
        # 重试：高质量
        return [
            make_node("公司差旅报销标准详细说明。" * 5, 0.78, source_path="报销制度.pdf"),
        ]


# ============================================================
# 1. 闲聊流程
# ============================================================

class TestChitchatFlow:
    """闲聊意图流程测试。"""

    def test_chitchat(self):
        """闲聊应直接用 LLM 回答，不检索。"""
        set_retrieve_fn(mock_retrieve_empty)
        result = run_agentic_rag("你好")
        assert result.intent == "chitchat"
        assert result.quality == "chitchat"
        assert result.retry_count == 0
        assert result.rewrite_used is False
        assert result.answer != ""


# ============================================================
# 2. 知识库问答流程
# ============================================================

class TestKnowledgeBaseFlow:
    """知识库问答流程测试。"""

    def test_good_retrieval(self):
        """高质量检索，直接生成回答。"""
        set_retrieve_fn(mock_retrieve_good)
        result = run_agentic_rag("报销制度里差旅标准是多少")
        assert result.intent == "knowledge_base"
        assert result.quality == "good"
        assert result.retry_count == 0
        assert result.rewrite_used is False
        assert result.top1_score > 0.5
        assert len(result.source_nodes) > 0
        print(f"  回答: {result.answer[:80]}")

    def test_bad_retrieval_with_retry(self):
        """低质量检索，触发改写重试。"""
        set_retrieve_fn(mock_retrieve_dynamic)
        result = run_agentic_rag("那个东西怎么弄")
        assert result.retry_count >= 1
        assert result.rewrite_used is True
        print(f"  原始: {result.original_query}")
        print(f"  改写: {result.rewritten_query}")
        print(f"  重试次数: {result.retry_count}")

    def test_empty_retrieval(self):
        """空检索结果，应提示无法回答。"""
        set_retrieve_fn(mock_retrieve_empty)
        result = run_agentic_rag("量子计算对区块链的影响")
        assert result.quality == "bad"
        assert "无法回答" in result.answer or "未检索到" in result.answer
        print(f"  回答: {result.answer[:80]}")


# ============================================================
# 3. 重试机制
# ============================================================

class TestRetryMechanism:
    """重试机制测试。"""

    def test_max_retry_zero(self):
        """max_retry=0 不应重试。"""
        set_retrieve_fn(mock_retrieve_bad)
        result = run_agentic_rag("测试问题", max_retry=0)
        assert result.retry_count == 0
        assert result.rewrite_used is False

    def test_retry_once_by_default(self):
        """默认最多重试 1 次。"""
        set_retrieve_fn(mock_retrieve_bad)
        result = run_agentic_rag("测试问题", max_retry=1)
        assert result.retry_count <= 1


# ============================================================
# 4. 结果结构
# ============================================================

class TestWorkflowResult:
    """结果数据结构测试。"""

    def test_result_fields(self):
        """结果应包含所有必要字段。"""
        set_retrieve_fn(mock_retrieve_good)
        result = run_agentic_rag("报销流程")
        assert hasattr(result, "answer")
        assert hasattr(result, "intent")
        assert hasattr(result, "route_method")
        assert hasattr(result, "quality")
        assert hasattr(result, "retry_count")
        assert hasattr(result, "rewrite_used")
        assert hasattr(result, "original_query")
        assert hasattr(result, "rewritten_query")
        assert hasattr(result, "source_nodes")
        assert hasattr(result, "top1_score")
        assert hasattr(result, "total_text_length")


# ============================================================
# 5. 生成回答（独立测试）
# ============================================================

class TestGenerateAnswer:
    """LLM 生成回答测试。"""

    def test_generate_with_nodes(self):
        """有检索结果时应生成回答。"""
        nodes = [
            make_node("差旅报销标准：住宿每晚不超过500元。" * 3, 0.8,
                      source_path="报销制度.pdf"),
        ]
        answer = generate_answer("差旅标准是多少", nodes)
        assert answer != ""
        assert len(answer) > 10
        print(f"  回答: {answer[:80]}")

    def test_generate_without_nodes(self):
        """无检索结果时应提示无法回答。"""
        answer = generate_answer("测试问题", [])
        assert answer != ""
        print(f"  回答: {answer[:80]}")
