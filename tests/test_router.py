"""Tests for the Agentic RAG query router.

Run:
    python -m pytest tests/test_router.py -q
"""

from app.agentic.router import IntentType, RouteResult, rule_based_route, route_query


def assert_rule_intent(query: str, expected: IntentType) -> RouteResult:
    result = rule_based_route(query)
    assert result is not None, f"expected rule hit for query: {query}"
    assert result.intent == expected
    assert result.method == "rule"
    return result


class TestChitchat:
    def test_pure_chitchat(self):
        for query in ["你好", "您好", "hello", "hi", "谢谢", "感谢", "再见", "你是谁"]:
            assert_rule_intent(query, IntentType.CHITCHAT)

    def test_chitchat_with_business_content_should_not_be_chitchat(self):
        for query in [
            "你好，解释一下 API Gateway 的限流策略",
            "hi，统计一下今天上传了多少文档",
            "谢谢，继续分析这份文档的审批流程",
        ]:
            result = rule_based_route(query)
            if result is not None:
                assert result.intent != IntentType.CHITCHAT


class TestSqlQuery:
    def test_sql_stat_query(self):
        for query in [
            "统计一下今天上传了多少文档",
            "查询最近考试的平均分",
            "用户总数是多少",
            "按访问量排行 top 10",
            "统计订单数量",
        ]:
            assert_rule_intent(query, IntentType.SQL_QUERY)

    def test_knowledge_question_with_stat_words_should_not_be_sql(self):
        for query in [
            "文档中提到的最大风险是什么",
            "这份报告里统计口径是怎么定义的",
            "知识库里说明的平均响应时间为什么会变高",
        ]:
            result = rule_based_route(query)
            if result is not None:
                assert result.intent != IntentType.SQL_QUERY


class TestWebSearch:
    def test_web_search(self):
        for query in [
            "今天 AI 圈有什么新闻",
            "最近 OpenAI 发布了什么",
            "现在英伟达股价是多少",
            "最新的 Milvus 版本是什么",
        ]:
            assert_rule_intent(query, IntentType.WEB_SEARCH)

    def test_document_scoped_latest_should_not_be_web_search(self):
        for query in [
            "这份文档中最新版本的 API 是多少",
            "知识库里最近一次制度更新是什么",
            "上传的文档里最新的审批流程是什么",
        ]:
            result = rule_based_route(query)
            if result is not None:
                assert result.intent != IntentType.WEB_SEARCH


class TestMultiStep:
    def test_multi_step(self):
        for query in [
            "对比 Nginx + Lua 和 Envoy Proxy 的优缺点，并给出迁移建议",
            "结合安全规范和 API 文档，分析这个方案的风险",
            "分别总结两个方案的核心差异，然后给出推荐",
            "分析这个系统的主要瓶颈、原因和优化步骤",
        ]:
            assert_rule_intent(query, IntentType.MULTI_STEP)

    def test_short_keyword_should_not_be_multi_step(self):
        for query in ["对比", "分析", "总结"]:
            result = rule_based_route(query)
            if result is not None:
                assert result.intent != IntentType.MULTI_STEP


class TestFallback:
    def test_empty_query(self):
        assert_rule_intent("", IntentType.CHITCHAT)
        assert_rule_intent("   ", IntentType.CHITCHAT)

    def test_common_knowledge_base_question_returns_none_for_llm_fallback(self):
        for query in [
            "Quantum API Gateway V3.5 为什么从 Nginx + Lua 替换为 Envoy Proxy？",
            "API Gateway 的限流策略是什么？",
            "文档里提到的权限审批流程是什么？",
        ]:
            assert rule_based_route(query) is None


class TestRouteQuery:
    def test_rule_layer_hits(self):
        cases = [
            ("你好", IntentType.CHITCHAT),
            ("统计一下今天上传了多少文档", IntentType.SQL_QUERY),
            ("今天 AI 圈有什么新闻", IntentType.WEB_SEARCH),
            ("对比 A 方案和 B 方案的优缺点，并给出建议", IntentType.MULTI_STEP),
        ]
        for query, expected in cases:
            result = route_query(query)
            assert result.intent == expected
            assert result.method == "rule"
            assert result.confidence == 1.0
