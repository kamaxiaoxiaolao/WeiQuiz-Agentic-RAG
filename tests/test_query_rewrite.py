"""Query Rewrite 测试用例

运行: pytest tests/test_query_rewrite.py -v
注意: LLM 相关测试需要 API Key，会跳过或 mock
"""

import pytest

from app.agentic.query_rewrite import (
    RewriteResult,
    fallback_rewrite,
    rewrite_query,
)


# ============================================================
# 1. 兜底改写 (规则，不依赖 LLM)
# ============================================================

class TestFallbackRewrite:
    """规则兜底改写测试。"""

    def test_replace_colloquial(self):
        """口语化表达应被替换。"""
        result = fallback_rewrite("报销怎么弄")
        assert "操作流程" in result.rewritten
        assert result.method == "fallback"
        assert result.success is True

    def test_remove_filler_words(self):
        """指代词应被去掉。"""
        result = fallback_rewrite("介绍一下这个报销制度")
        assert "这个" not in result.rewritten
        assert "报销" in result.rewritten

    def test_preserve_core_content(self):
        """核心内容应保留。"""
        result = fallback_rewrite("差旅报销标准是多少")
        assert "差旅" in result.rewritten
        assert "报销" in result.rewritten
        assert "标准" in result.rewritten

    def test_keywords_extraction(self):
        """应提取出关键词列表。"""
        result = fallback_rewrite("公司报销制度差旅标准")
        assert len(result.keywords) > 0
        assert any("报销" in kw for kw in result.keywords)

    def test_no_colloquial(self):
        """无口语化表达时应保持原样。"""
        result = fallback_rewrite("报销流程是什么")
        assert result.rewritten == "报销流程是什么"
        assert result.method == "fallback"

    def test_multiple_colloquial(self):
        """多个口语化表达应全部替换。"""
        result = fallback_rewrite("报销怎么弄，差旅咋办")
        assert "操作流程" in result.rewritten
        assert "解决方法" in result.rewritten

    def test_clean_extra_spaces(self):
        """应清理多余空格。"""
        result = fallback_rewrite("报销  制度  说明")
        assert "  " not in result.rewritten

    def test_english_keywords(self):
        """应支持英文关键词。"""
        result = fallback_rewrite("API接口文档")
        assert any("API" in kw for kw in result.keywords)


# ============================================================
# 2. LLM 改写 (需要 API)
# ============================================================

class TestLLMRewrite:
    """LLM 改写测试（需要 API Key）。"""

    def test_rewrite_basic(self):
        """基本改写功能。"""
        result = rewrite_query("报销制度里差旅标准是多少")
        assert result.success is True
        assert result.rewritten != ""
        assert len(result.keywords) > 0
        print(f"  原始: {result.original}")
        print(f"  改写: {result.rewritten}")
        print(f"  关键词: {result.keywords}")

    def test_rewrite_ambiguous(self):
        """模糊问题改写。"""
        result = rewrite_query("那个东西怎么弄")
        assert result.success is True
        assert result.rewritten != ""
        print(f"  原始: {result.original}")
        print(f"  改写: {result.rewritten}")

    def test_rewrite_already_good(self):
        """已经适合检索的问题。"""
        result = rewrite_query("差旅报销标准")
        assert result.success is True
        assert result.rewritten != ""
        print(f"  原始: {result.original}")
        print(f"  改写: {result.rewritten}")


# ============================================================
# 3. 改写结果结构
# ============================================================

class TestRewriteResult:
    """改写结果数据结构测试。"""

    def test_result_fields(self):
        """结果应包含所有必要字段。"""
        result = fallback_rewrite("测试问题")
        assert hasattr(result, "original")
        assert hasattr(result, "rewritten")
        assert hasattr(result, "keywords")
        assert hasattr(result, "method")
        assert hasattr(result, "success")

    def test_result_original_preserved(self):
        """原始问题应被保留。"""
        result = fallback_rewrite("原始问题")
        assert result.original == "原始问题"
