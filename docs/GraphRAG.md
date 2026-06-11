# GraphRAG 学习与项目实现计划

本文档用于整理 GraphRAG 的核心知识点，并规划 WeiQuiz Agentic RAG 项目如何逐步引入 GraphRAG。目标不是盲目堆一个“知识图谱”名词，而是围绕 RAG 面试和项目真实收益，明确 GraphRAG 解决什么问题、怎么评估、怎么和现有 Hybrid Retrieval / AgentController / Phoenix 可观测链路结合。

## 1. 为什么要做 GraphRAG

当前 WeiQuiz 的主检索链路是 chunk-based RAG：

```text
文档解析
  -> hierarchical chunk
  -> leaf chunk 向量化
  -> Dense + BM25
  -> RRF
  -> Rerank
  -> Parent Context / Auto-merging
  -> 生成答案
```

这条链路适合回答“某个片段里能找到答案”的问题，但对以下场景能力不足：

| 场景 | 普通 RAG 的问题 | GraphRAG 的补充价值 |
| --- | --- | --- |
| 跨文档实体关联 | 不同文档分别提到同一实体，chunk 检索可能召回不完整 | 用实体节点把跨文档信息连接起来 |
| 多跳关系问题 | 问题需要 A -> B -> C 的关系链 | 用图路径检索补充结构化推理链 |
| 全局总结问题 | “这个系统有哪些模块和依赖关系”不是单个 chunk 能回答 | 用社区摘要 / 子图摘要提供全局上下文 |
| 实体中心问答 | 用户围绕某个产品、接口、模块持续提问 | 以实体为中心聚合相关 chunk 和关系 |
| 可解释性 | 向量召回只能解释“相似”，不能解释“为什么相关” | 图路径可以展示实体关系和证据来源 |

## 2. GraphRAG 到底是什么

GraphRAG 可以理解为：

```text
传统 RAG：query -> chunk retrieval -> answer
GraphRAG：query -> entity / relation / subgraph retrieval -> text evidence -> answer
```

它不是替代向量检索，而是给 RAG 增加一个结构化知识层：

```text
Chunk Index：保存原文证据
Vector Index：负责语义召回
Sparse Index：负责关键词召回
Graph Index：保存实体、关系、路径和社区摘要
```

更准确地说，GraphRAG 常见有三种形态：

| 形态 | 核心思想 | 适合场景 |
| --- | --- | --- |
| Entity Graph RAG | 抽取实体和实体关系，检索实体邻居与路径 | 多跳问答、实体关系问答 |
| Community Summary GraphRAG | 对实体图做社区发现，为社区生成摘要 | 全局总结、主题归纳、跨文档报告 |
| Graph + Vector Hybrid RAG | 先向量召回 chunk，再扩展相关实体/邻居/路径 | 工程落地、与现有 RAG 兼容 |

## 3. 主流实现路线

### 3.1 Microsoft GraphRAG 路线

Microsoft GraphRAG 更偏重“从非结构化文本构建图谱，再做社区摘要和全局查询”：

```text
Text Units
  -> Entity Extraction
  -> Relationship Extraction
  -> Graph Construction
  -> Community Detection
  -> Community Summary
  -> Local Search / Global Search
```

适合学习的重点：

| 知识点 | 要理解的问题 |
| --- | --- |
| Entity extraction | 如何从 chunk 中抽取实体 |
| Relationship extraction | 如何抽取实体之间的关系 |
| Community detection | 为什么要把图划分成社区 |
| Community report | 社区摘要如何支持全局问答 |
| Local search | 围绕实体局部扩展上下文 |
| Global search | 汇总多个社区摘要回答宏观问题 |

### 3.2 LlamaIndex PropertyGraph 路线

LlamaIndex 的 PropertyGraph 更适合和当前项目结合，因为 WeiQuiz 已经大量使用 LlamaIndex。

适合学习的重点：

| 知识点 | 要理解的问题 |
| --- | --- |
| PropertyGraphIndex | LlamaIndex 如何表示实体、关系和属性 |
| Graph Store | 图数据怎么存储，简单实现和外部图数据库有什么区别 |
| Path retrieval | 如何从实体出发找关系路径 |
| Text-to-Cypher / graph query | 如何让 LLM 生成图查询 |
| Graph + Vector retriever | 如何把图检索和向量检索融合 |

### 3.3 Neo4j GraphRAG 路线

Neo4j 更偏工程生产实现，适合后期扩展：

```text
Neo4j Graph DB
  -> entity nodes
  -> relationship edges
  -> graph traversal
  -> graph + vector retrieval
```

适合学习的重点：

| 知识点 | 要理解的问题 |
| --- | --- |
| 节点与关系建模 | 实体类型、关系类型、属性如何设计 |
| Cypher 查询 | 如何查实体邻居、路径和子图 |
| Graph traversal | 多跳路径怎么限制深度和噪声 |
| Vector + Graph | 向量召回 chunk 后如何扩展实体邻居 |
| Graph database tradeoff | 为什么图数据库增强能力强，但工程复杂度也高 |

## 4. 需要学习的知识点

### 4.1 图基础

| 知识点 | 学习目标 |
| --- | --- |
| Node / Edge / Property | 能解释实体、关系、属性分别是什么 |
| Directed graph | 能解释关系方向为什么重要 |
| Path / Neighbor / Degree | 能解释路径检索和邻居扩展 |
| Subgraph | 能解释为什么最终给 LLM 的不是整张图，而是相关子图 |
| Community | 能解释社区发现为什么适合全局总结 |

### 4.2 信息抽取

| 知识点 | 学习目标 |
| --- | --- |
| Entity extraction | 从 chunk 中抽取模块、接口、产品、角色、状态码等 |
| Relation extraction | 抽取“依赖”“属于”“调用”“导致”“包含”“约束”等关系 |
| Entity normalization | 同一个实体不同叫法如何合并，如 JWT Token / 登录凭证 |
| Relation confidence | 关系是否可信，是否需要分数 |
| Source grounding | 每个实体和关系必须能追溯到 chunk_id / doc_id |

### 4.3 图谱构建

| 知识点 | 学习目标 |
| --- | --- |
| Schema-first vs Open schema | 先定义实体类型，还是让 LLM 自由抽取 |
| Incremental update | 文档更新后如何删除旧实体关系 |
| Deduplication | 如何合并重复实体 |
| Provenance | 如何保存关系来源证据 |
| Graph persistence | JSON / SQLite / PostgreSQL / Neo4j 怎么选 |

### 4.4 图检索

| 知识点 | 学习目标 |
| --- | --- |
| Entity linking | 用户问题里的实体如何匹配图谱实体 |
| Local graph retrieval | 从实体出发找一跳/两跳邻居 |
| Path retrieval | 找 A 到 B 的关系链 |
| Community retrieval | 根据问题找相关社区摘要 |
| Graph + Vector fusion | 图谱结果和向量结果如何融合 |

### 4.5 评估与风险

| 知识点 | 学习目标 |
| --- | --- |
| Relation precision | 抽取出的关系是否正确 |
| Entity recall | 问题涉及的实体是否被图谱覆盖 |
| Path correctness | 多跳路径是否真实有效 |
| Context usefulness | 图谱上下文是否真的提升回答 |
| Cost / latency | 图谱抽取和检索是否值得 |

## 5. WeiQuiz 中的 GraphRAG 目标架构

GraphRAG 不应该替换当前 Hybrid Retrieval，而应该作为一个新的检索通道：

```text
用户问题
  -> AgentController
  -> Query Planning
  -> 判断是否需要 GraphRAG
  -> 并行或串行执行：
       Dense Retrieval
       BM25 Retrieval
       Graph Retrieval
  -> RRF / 自定义融合
  -> Rerank
  -> Generation
  -> Grounding
```

建议目标架构：

```text
app/
  graph/
    schema.py          图谱数据结构：Entity / Relation / GraphEvidence
    extractor.py       从 chunk 中抽取实体和关系
    normalizer.py      实体归一化与去重
    store.py           图谱存储，第一版用 SQLite/PostgreSQL
    retriever.py       图谱检索：entity search / neighbor / path
    prompts.py         抽取、归一化、图谱问答 prompt
    service.py         GraphRAGService，对外提供 build / search

  agentic/
    controller.py      增加 graph_search 策略判断
    router.py          增加 GraphRAG query_strategy

  tools/
    registry.py        注册 graph_search 工具
```

## 6. 项目实现路线

### Phase 0：只学习与设计，不写重功能

目标：能讲清楚 GraphRAG 是什么、解决什么问题、和当前 RAG 怎么结合。

| 任务 | 输出 |
| --- | --- |
| 学习 Microsoft GraphRAG / LlamaIndex PropertyGraph / Neo4j GraphRAG | 整理本文档 |
| 梳理适合 WeiQuiz 的实体类型 | 初版 ontology |
| 梳理适合 WeiQuiz 的关系类型 | 初版 relation schema |
| 明确 GraphRAG 不替代 Hybrid Retrieval | 架构说明 |

建议实体类型：

| 类型 | 示例 |
| --- | --- |
| System | WeiQuiz、AuthService、Quantum API Gateway |
| Module | Router、MemoryService、Retriever、Rerank |
| API | `/auth/login`、`/chat/stream` |
| Concept | JWT、RBAC、BM25、RRF、Auto-merging |
| ErrorCode | 401、403、500 |
| Document | 技术文档、制度文档、课程资料 |

建议关系类型：

| 关系 | 含义 |
| --- | --- |
| CONTAINS | 文档/系统包含模块、API、概念 |
| DEPENDS_ON | 模块依赖另一个模块 |
| CALLS | API 或服务调用另一个服务 |
| PRODUCES | 模块产生某种输出 |
| PROTECTS | 权限/安全机制保护资源 |
| CAUSES | 某个条件导致某个结果 |
| MAPS_TO | 概念或错误类型映射到状态码/处理方式 |

### Phase 1：轻量图谱抽取与存储

目标：从已有 chunk / parent store 中抽取实体和关系，形成可查询图谱。

| 任务 | 实现 |
| --- | --- |
| 新增 `app/graph/schema.py` | 定义 Entity、Relation、GraphEvidence |
| 新增 `app/graph/extractor.py` | 用 LLM 从 chunk 中抽取 JSON |
| 新增 `app/graph/store.py` | 第一版用 SQLite 或 PostgreSQL 表保存 |
| 入库后触发图谱构建 | 文档新增/更新时同步更新图谱 |
| 每条关系保存来源 | `doc_id`、`chunk_id`、`source_text`、`confidence` |

第一版抽取输出格式：

```json
{
  "entities": [
    {
      "name": "AuthService V2",
      "type": "System",
      "aliases": ["认证服务"]
    }
  ],
  "relations": [
    {
      "source": "AuthService V2",
      "target": "401 Unauthorized",
      "type": "MAPS_TO",
      "description": "Token 过期或签名失败时返回 401",
      "confidence": 0.86
    }
  ]
}
```

### Phase 2：图谱检索工具

目标：让 Agent 能调用图谱检索，而不是只做向量检索。

| 任务 | 实现 |
| --- | --- |
| Entity linking | 从用户问题中识别实体并匹配图谱实体 |
| Neighbor retrieval | 找实体一跳/两跳邻居 |
| Path retrieval | 查两个实体之间的路径 |
| Graph evidence formatting | 把图谱结果格式化成 LLM 上下文 |
| 注册 `graph_search` 工具 | 让 AgentController 可调度 |

示例：

```text
问题：AuthService V2 中 Token 过期和权限不足分别对应什么状态码？

Graph retrieval:
AuthService V2 -> MAPS_TO -> 401 Unauthorized
AuthService V2 -> MAPS_TO -> 403 Forbidden
401 Unauthorized -> CAUSED_BY -> Token 过期/签名失败
403 Forbidden -> CAUSED_BY -> RBAC 权限不足
```

### Phase 3：Graph + Vector 融合

目标：GraphRAG 不单独回答，而是和现有 Hybrid Retrieval 融合。

| 任务 | 实现 |
| --- | --- |
| 向量召回后实体扩展 | 从 top chunks 抽取实体，扩展图谱邻居 |
| 图谱召回后文本回填 | 根据 graph evidence 的 chunk_id 回取原文 |
| Graph evidence 参与 rerank | 图谱上下文与 chunk 一起进入精排 |
| Trace 展示 graph steps | 前端和 Phoenix 展示 GraphRAG 检索步骤 |

融合方式建议：

```text
Vector/BM25 负责找文本证据
Graph 负责找实体关系和多跳路径
Rerank 负责最终上下文排序
Grounding 负责检查答案是否被证据支撑
```

### Phase 4：社区摘要与全局问答

目标：支持“总结整个系统结构、模块依赖、制度关系”这类全局问题。

| 任务 | 实现 |
| --- | --- |
| Graph community detection | 对实体图做社区划分 |
| Community summary | 为每个社区生成摘要 |
| Global query | 根据问题检索多个社区摘要 |
| Map-reduce answer | 汇总社区摘要生成最终回答 |

这一步属于高级能力，不建议早于 Phase 1-3。

### Phase 5：评估与可观测

目标：证明 GraphRAG 是否真的有效，而不是只多了复杂度。

| 任务 | 指标 |
| --- | --- |
| 图谱抽取评估 | entity precision、relation precision |
| 图谱检索评估 | entity hit、path hit、graph context recall |
| Graph + Vector 对比 | dense / hybrid / graph / graph+hybrid |
| 生成质量评估 | faithfulness、answer relevance |
| Phoenix Trace | 记录 graph extraction、graph retrieval、path expansion |

## 7. AgentController 如何选择 GraphRAG

GraphRAG 不应该所有问题都走，因为它会增加成本和延迟。

建议触发条件：

| 问题特征 | 策略 |
| --- | --- |
| “A 和 B 有什么关系” | graph_search |
| “某模块依赖哪些模块” | graph_search |
| “从 X 到 Y 的链路是什么” | path retrieval |
| “总结整个系统模块关系” | community summary |
| 普通事实问答 | vector + BM25 |
| 模糊语义问题 | HyDE / Step-back |

Router / Controller 可新增：

```text
QueryStrategy.GRAPH_SEARCH
QueryStrategy.GRAPH_HYBRID
```

## 8. 技术选型建议

### 第一阶段推荐

```text
存储：PostgreSQL 或 SQLite
图处理：NetworkX
抽取：OpenAI-compatible LLM structured output
检索：Entity linking + neighbor/path retrieval
```

原因：

| 优点 | 说明 |
| --- | --- |
| 轻量 | 不需要立刻引入 Neo4j |
| 易调试 | 可以直接看表和 JSON |
| 和当前项目一致 | 当前已有 PostgreSQL、LlamaIndex、Phoenix |
| 面试够讲 | 能讲清楚 GraphRAG 核心链路 |

### 后期可选

| 方案 | 适用条件 |
| --- | --- |
| Neo4j | 图谱规模变大，需要 Cypher 和图遍历 |
| LlamaIndex PropertyGraphIndex | 希望更深集成 LlamaIndex |
| Microsoft GraphRAG | 需要社区摘要和全局报告问答 |
| NebulaGraph | 企业级分布式图数据库场景 |

## 9. 风险与取舍

GraphRAG 不是一定比普通 RAG 好。

| 风险 | 说明 | 控制方式 |
| --- | --- | --- |
| 抽取错误 | LLM 可能抽出不存在的关系 | 每条关系保存 source evidence，低置信度不入库 |
| 图谱噪声 | 实体太多、关系太泛会污染检索 | 限制实体类型和关系类型 |
| 成本增加 | 抽取实体关系需要额外 LLM 调用 | 入库阶段离线抽取，不放在实时问答路径 |
| 延迟增加 | 图谱检索和融合增加链路复杂度 | 只对关系型/多跳问题触发 |
| 评估困难 | 难证明图谱真的提升 | 做 graph/hybrid ablation 和 badcase 对比 |

## 10. 面试回答模板

### 10.1 GraphRAG 是什么

> GraphRAG 是在传统 chunk-based RAG 之外增加实体关系图谱层。普通 RAG 主要通过向量相似度召回文本片段，而 GraphRAG 会把文档中的实体、关系和路径抽取出来，用结构化关系辅助检索。它更适合跨文档实体关联、多跳关系问答和全局总结类问题。

### 10.2 为什么你的项目需要 GraphRAG

> 当前项目已经有 Dense + BM25 + RRF + Rerank，能解决大多数单跳文本证据检索问题。但如果问题是“某个模块依赖哪些服务”“A 和 B 的关系链是什么”“整个系统有哪些模块关系”，单纯 chunk 检索可能召回不完整。因此我计划引入 GraphRAG，把实体关系作为新的检索通道，再和原有 Hybrid Retrieval 融合。

### 10.3 GraphRAG 怎么和当前 RAG 融合

> 我不会用 GraphRAG 替代当前向量检索，而是把它作为额外检索通道。向量和 BM25 负责找文本证据，图谱检索负责找实体关系和多跳路径，最后通过 Rerank 和 Grounding 控制上下文质量与答案忠实度。这样既保留原文证据，又增强结构化推理能力。

### 10.4 为什么不一开始就用 Neo4j

> Neo4j 适合图规模较大、路径查询复杂的生产场景，但对当前个人项目来说会引入额外基础设施和调试成本。第一版我会先用 PostgreSQL/SQLite 保存实体和关系，用 NetworkX 做轻量图遍历，等验证 GraphRAG 对检索质量有提升后，再考虑接 Neo4j。

### 10.5 怎么证明 GraphRAG 有效

> 我会做检索消融实验：dense only、hybrid、graph only、graph + hybrid，对比多跳问题和关系型问题上的 Hit@K、MRR、path hit，并通过 Phoenix trace 观察 GraphRAG 召回的实体路径是否真的进入最终上下文。如果指标和 badcase 都没有明显改善，就不会默认开启 GraphRAG。

## 11. 开发优先级

| 优先级 | 任务 | 原因 |
| --- | --- | --- |
| P0 | 学习与方案文档 | 先能讲清楚，不急着堆代码 |
| P1 | 轻量实体/关系抽取 | GraphRAG 的基础 |
| P1 | 图谱存储与检索 | 形成可运行 GraphRAG 通道 |
| P1 | Graph + Hybrid 融合 | 与当前项目主链路结合 |
| P2 | Phoenix Graph span | 可观测和面试展示 |
| P2 | 图谱评估集 | 证明 GraphRAG 有效 |
| P3 | Neo4j / Community Summary | 高级扩展，不是第一阶段 |

## 12. 推荐下一步

当前项目已经具备：

```text
文档解析
chunk / parent store
Hybrid Retrieval
AgentController
Phoenix Observability
```

因此 GraphRAG 的下一步不应该直接上 Neo4j，而是：

```text
1. 定义 WeiQuiz 图谱实体和关系 schema
2. 新建 app/graph/schema.py
3. 从 parent/leaf chunk 中离线抽取实体关系
4. 用 PostgreSQL/SQLite 保存 graph triples
5. 实现 graph_search(query)
6. 再接入 AgentController
```

第一版目标只要能回答这类问题即可：

```text
AuthService V2 中 Token 过期和权限不足分别对应什么状态码？
WeiQuiz 的 MemoryService、Redis、PostgreSQL 分别是什么关系？
AgentController 和 RAG Workflow 的职责边界是什么？
某个模块依赖哪些组件？
```

## 13. 参考资料

- Microsoft GraphRAG 官方概览：https://microsoft.github.io/graphrag//index/overview/
- LlamaIndex PropertyGraphIndex 文档：https://docs.llamaindex.ai/en/stable/api_reference/indices/property_graph/
- Neo4j GraphRAG Labs：https://neo4j.com/labs/genai-ecosystem/graphrag/
- Neo4j GraphRAG 介绍：https://neo4j.com/blog/genai/what-is-graphrag/
- Neo4j GraphAcademy GraphRAG：https://graphacademy.neo4j.com/courses/genai-fundamentals/2-rag/4-graphrag/
