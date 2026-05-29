"""Retrieval Quality Check — 检索质量评估

评估检索结果是否足够回答用户问题。
输入: LlamaIndex 的 source_nodes (List[NodeWithScore])
输出: QualityResult (good / bad + 原因)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ============================================================
# 1. 配置阈值
# ============================================================

@dataclass
class QualityThresholds:
    """质量阈值配置，可根据场景调整。"""
    min_top1_score: float = 0.3        # top1 最低 rerank 分数
    min_total_text_length: int = 100   # 有效文本总长度最小值 (字符)
    min_node_count: int = 1            # 最少命中节点数
    max_source_diversity: int = 5      # 来源文档数超过此值认为过于分散


# ============================================================
# 2. 质量结果
# ============================================================

@dataclass
class QualityResult:
    """质量评估结果。"""
    quality: str                         # "good" | "bad"
    reason: str                          # 评估依据
    top1_score: Optional[float] = None   # top1 rerank 分数
    node_count: int = 0                  # 命中节点数
    total_text_length: int = 0           # 有效文本总长度
    source_count: int = 0                # 来源文档数
    auto_merged: bool = False            # 是否触发 AutoMerging
    should_retry: bool = False           # 建议是否重试
    suggested_top_k: int = 5             # 重试时建议的 top_k


# ============================================================
# 3. 核心评估逻辑
# ============================================================

def check_retrieval_quality(
    source_nodes: list,
    thresholds: Optional[QualityThresholds] = None,
) -> QualityResult:
    """评估检索结果质量。

    Args:
        source_nodes: LlamaIndex 检索结果 (response.source_nodes)
        thresholds: 质量阈值配置，不传则使用默认值

    Returns:
        QualityResult 包含质量评级、原因、是否建议重试
    """
    if thresholds is None:
        thresholds = QualityThresholds()

    # ---------- 空结果 ----------
    if not source_nodes:
        return QualityResult(
            quality="bad",
            reason="检索结果为空",
            node_count=0,
            total_text_length=0,
            should_retry=True,
            suggested_top_k=10,
        )

    # ---------- 提取指标 ----------
    top1_score = source_nodes[0].score or 0.0
    node_count = len(source_nodes)

    # 有效文本总长度
    total_text_length = sum(
        len(node.text.strip()) for node in source_nodes if node.text
    )

    # 来源文档数 (去重)
    source_paths = set()
    for node in source_nodes:
        path = node.metadata.get("source_path", "")
        if path:
            source_paths.add(path)
    source_count = len(source_paths)

    # AutoMerging 是否触发
    auto_merged = any(
        node.metadata.get("auto_merged", False) for node in source_nodes
    )

    # ---------- 逐项检查 ----------

    # Check 1: top1 分数过低
    if top1_score < thresholds.min_top1_score:
        return QualityResult(
            quality="bad",
            reason=f"top1 分数过低: {top1_score:.3f} < {thresholds.min_top1_score}",
            top1_score=top1_score,
            node_count=node_count,
            total_text_length=total_text_length,
            source_count=source_count,
            auto_merged=auto_merged,
            should_retry=True,
            suggested_top_k=10,
        )

    # Check 2: 有效文本太短
    if total_text_length < thresholds.min_total_text_length:
        return QualityResult(
            quality="bad",
            reason=f"有效文本过短: {total_text_length} 字 < {thresholds.min_total_text_length} 字",
            top1_score=top1_score,
            node_count=node_count,
            total_text_length=total_text_length,
            source_count=source_count,
            auto_merged=auto_merged,
            should_retry=True,
            suggested_top_k=10,
        )

    # Check 3: 来源过于分散 (可选，不阻断但标记)
    source_warning = ""
    if source_count > thresholds.max_source_diversity:
        source_warning = f" (注意: 来源分散, 涉及 {source_count} 个文档)"

    # ---------- 质量合格 ----------
    logger.info(
        "[Quality] good | top1=%.3f | nodes=%d | text_len=%d | sources=%d | merged=%s%s",
        top1_score, node_count, total_text_length, source_count, auto_merged, source_warning,
    )

    return QualityResult(
        quality="good",
        reason=f"质量合格{source_warning}",
        top1_score=top1_score,
        node_count=node_count,
        total_text_length=total_text_length,
        source_count=source_count,
        auto_merged=auto_merged,
        should_retry=False,
    )
