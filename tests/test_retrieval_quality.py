"""Retrieval Quality Check 测试用例

运行: pytest tests/test_retrieval_quality.py -v
"""

import pytest
from dataclasses import dataclass
from typing import Optional

from app.agentic.retrieval_quality import (
    check_retrieval_quality,
    QualityResult,
    QualityThresholds,
)


# ============================================================
# Mock NodeWithScore (不依赖 LlamaIndex)
# ============================================================

@dataclass
class MockNode:
    """模拟 LlamaIndex TextNode。"""
    text: str = ""
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class MockNodeWithScore:
    """模拟 LlamaIndex NodeWithScore。"""
    node: MockNode
    score: Optional[float] = None

    @property
    def text(self):
        return self.node.text

    @property
    def metadata(self):
        return self.node.metadata


def make_node(text: str, score: float, **metadata) -> MockNodeWithScore:
    """快捷创建测试节点。"""
    return MockNodeWithScore(
        node=MockNode(text=text, metadata=metadata),
        score=score,
    )


# ============================================================
# 1. 空结果
# ============================================================

class TestEmptyResults:
    """空检索结果测试。"""

    def test_empty_list(self):
        """空列表应返回 bad。"""
        result = check_retrieval_quality([])
        assert result.quality == "bad"
        assert result.should_retry is True
        assert result.node_count == 0
        assert "空" in result.reason

    def test_none_list(self):
        """None 应返回 bad。"""
        result = check_retrieval_quality(None)
        assert result.quality == "bad"
        assert result.should_retry is True


# ============================================================
# 2. top1 分数过低
# ============================================================

class TestTop1Score:
    """top1 rerank 分数测试。"""

    def test_low_score(self):
        """top1 分数低于阈值应返回 bad。"""
        nodes = [
            make_node("这是一段测试文本内容，长度足够用于测试。" * 5, 0.15),
            make_node("另一段测试文本。" * 3, 0.10),
        ]
        result = check_retrieval_quality(nodes)
        assert result.quality == "bad"
        assert result.should_retry is True
        assert "分数过低" in result.reason
        assert result.top1_score == 0.15

    def test_boundary_score(self):
        """top1 分数刚好等于阈值应返回 good。"""
        nodes = [
            make_node("这是一段测试文本内容，长度足够用于质量评估和检查。" * 5, 0.3),
        ]
        result = check_retrieval_quality(nodes)
        assert result.quality == "good"
        assert result.top1_score == 0.3

    def test_high_score(self):
        """top1 分数高于阈值应返回 good。"""
        nodes = [
            make_node("这是一段测试文本内容，长度足够用于质量评估检查。" * 5, 0.85),
        ]
        result = check_retrieval_quality(nodes)
        assert result.quality == "good"
        assert result.top1_score == 0.85


# ============================================================
# 3. 有效文本长度
# ============================================================

class TestTextLength:
    """有效文本长度测试。"""

    def test_short_text(self):
        """文本太短应返回 bad。"""
        nodes = [
            make_node("短文本", 0.8),  # 3 字，远低于 100 字阈值
        ]
        result = check_retrieval_quality(nodes)
        assert result.quality == "bad"
        assert "文本过短" in result.reason
        assert result.total_text_length < 100

    def test_empty_text(self):
        """空文本应返回 bad。"""
        nodes = [
            make_node("", 0.9),
            make_node("   ", 0.8),  # 纯空格
        ]
        result = check_retrieval_quality(nodes)
        assert result.quality == "bad"

    def test_enough_text(self):
        """文本足够长应返回 good。"""
        nodes = [
            make_node("这是一段足够长的测试文本内容。" * 10, 0.7),
        ]
        result = check_retrieval_quality(nodes)
        assert result.quality == "good"
        assert result.total_text_length >= 100


# ============================================================
# 4. 综合场景
# ============================================================

class TestCombinedScenarios:
    """综合场景测试。"""

    def test_multiple_good_nodes(self):
        """多个高质量节点应返回 good。"""
        nodes = [
            make_node("第一段检索结果，内容丰富且相关。" * 5, 0.85, source_path="doc1.pdf"),
            make_node("第二段检索结果，补充说明。" * 5, 0.72, source_path="doc1.pdf"),
            make_node("第三段检索结果，额外信息。" * 5, 0.65, source_path="doc2.pdf"),
        ]
        result = check_retrieval_quality(nodes)
        assert result.quality == "good"
        assert result.node_count == 3
        assert result.source_count == 2
        assert result.should_retry is False

    def test_mixed_quality_nodes(self):
        """混合质量节点，以 top1 为准。"""
        nodes = [
            make_node("高质量结果。" * 20, 0.8),
            make_node("低质量结果", 0.1),
        ]
        result = check_retrieval_quality(nodes)
        assert result.quality == "good"  # top1 分数足够高
        assert result.top1_score == 0.8

    def test_auto_merged_flag(self):
        """AutoMerging 触发时应记录。"""
        nodes = [
            make_node("合并后的父节点文本内容。" * 10, 0.7, auto_merged=True),
        ]
        result = check_retrieval_quality(nodes)
        assert result.auto_merged is True

    def test_source_diversity_warning(self):
        """来源过于分散应标记警告（但仍为 good）。"""
        nodes = [
            make_node(f"来自文档{i}的检索结果内容。" * 5, 0.7, source_path=f"doc{i}.pdf")
            for i in range(6)
        ]
        result = check_retrieval_quality(nodes)
        assert result.quality == "good"  # 不阻断
        assert result.source_count == 6
        assert "来源分散" in result.reason


# ============================================================
# 5. 自定义阈值
# ============================================================

class TestCustomThresholds:
    """自定义阈值测试。"""

    def test_strict_thresholds(self):
        """更严格的阈值。"""
        strict = QualityThresholds(min_top1_score=0.5, min_total_text_length=200)
        nodes = [
            make_node("测试文本。" * 10, 0.4),  # 分数 0.4 < 0.5
        ]
        result = check_retrieval_quality(nodes, thresholds=strict)
        assert result.quality == "bad"

    def test_relaxed_thresholds(self):
        """更宽松的阈值。"""
        relaxed = QualityThresholds(min_top1_score=0.1, min_total_text_length=10)
        nodes = [
            make_node("短文本", 0.15),  # 分数 0.15 > 0.1, 长度 3 > 10? 不够
        ]
        result = check_retrieval_quality(nodes, thresholds=relaxed)
        assert result.quality == "bad"  # 文本长度仍不够
        assert "文本过短" in result.reason
