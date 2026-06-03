# RAG / Agentic RAG 面试常问问题与项目优化路线

这份文档用于把面试高频问题和 WeiQuiz 项目后续优化绑定起来。目标不是盲目堆功能，而是围绕面试官最常追问的核心点，一边补强项目，一边形成能讲清楚的标准回答。

## 1. 使用方式

后续每次优化项目时，都优先检查它能回答哪类面试问题。

```text
面试问题
  -> 当前项目实现
  -> 差距
  -> 优化任务
  -> 面试回答沉淀
```

优先级原则：

| 优先级 | 判断标准 |
| --- | --- |
| P0 | 面试高频、和 RAG 主链路强相关、做完能显著提升项目可信度 |
| P1 | 有高级感，能体现工程深度，但不是当前系统短板 |
| P2 | 适合写未来规划，不建议现在投入太多时间 |

## 2. RAG 整体链路

### 常问问题

| 问题 | 当前项目能否回答 | 优化方向 |
| --- | --- | --- |
| 你的 RAG 系统整体架构是什么？ | 能 | 持续维护项目总结文档和架构图 |
| 用户提问后，从输入到答案生成经历了哪些阶段？ | 能 | 保持前端 Trace 和后端日志一致 |
| 普通 RAG 和 Agentic RAG 的区别是什么？ | 能 | 用 AgentController + Workflow 举例说明 |
| 你项目里哪些地方体现 Agentic？ | 能 | 强化 Query Planning、Rewrite、Quality Check、Grounding 的边界 |

### 当前项目实现

```text
用户问题
  -> FastAPI /chat/stream
  -> AgentController 决策
  -> Memory Context
  -> Fast Path 或 Agentic RAG Workflow
  -> Hybrid Retrieval
  -> Rerank / Auto-merging
  -> Answer Generation
  -> Optional Grounding
  -> SSE 流式返回
  -> PostgreSQL / Redis 保存会话
```

### 下一步优化

| 优先级 | 任务 |
| --- | --- |
| P0 | 把当前链路沉淀成一张简洁架构图，方便面试复述 |
| P1 | 把 AgentController 的输入输出结构写进文档 |

## 3. 文档解析与 Chunk

### 常问问题

| 问题 | 当前项目能否回答 | 优化方向 |
| --- | --- | --- |
| 文档入库流程怎么做？ | 能 | 结合 ingestion report 讲 |
| PDF / Word / Markdown 怎么解析？ | 能 | 补充不同格式解析策略 |
| chunk size 和 overlap 怎么选？ | 能 | 用父子块策略回答，不陷入固定参数 |
| 为什么做 parent-child chunk？ | 能 | 强调小块检索、大块生成 |
| leaf chunk 和 parent chunk 分别存在哪里？ | 能 | leaf 入 pgvector/Milvus，parent/root 入 PostgreSQL |
| 表格、图片、跨页表格怎么处理？ | 部分能 | 重点补结构化表格和跨页表格策略 |
| 文档更新和删除后，向量库怎么同步？ | 能 | 结合 SHA256 diff、doc_id、BM25 increment_remove 讲 |

### 当前项目实现

```text
扫描 docs_dir
  -> SHA256 diff
  -> parse file to blocks
  -> clean blocks
  -> section merge
  -> hierarchical chunk
  -> leaf nodes 写入 pgvector/Milvus
  -> root/parent/leaf 写入 PostgreSQL parent store
  -> 生成 ingestion_report_latest.json
```

### 下一步优化

| 优先级 | 任务 |
| --- | --- |
| P0 | 整理跨页表格、图片 OCR、代码块、公式的处理策略文档 |
| P1 | 给表格 block 增加关键 metadata，如 table_id、page_range、headers |
| P1 | 入库报告中增加“疑似表格断裂 / 跨页表格”检测结果 |

## 4. 向量检索与 pgvector/Milvus

### 常问问题

| 问题 | 当前项目能否回答 | 优化方向 |
| --- | --- | --- |
| 为什么本地默认 pgvector？ | 能 | 从本地部署简单、PostgreSQL 一体化、减少依赖回答 |
| 为什么保留 Milvus 可选？ | 能 | 从大规模向量检索、独立向量服务、生产扩展回答 |
| embedding 模型怎么选？ | 能 | 需要结合数据集和评估讲 |
| top_k 怎么选？ | 能 | 需要用评估闭环支撑 |
| 相似度用 cosine、IP 还是 L2？ | 需要准备 | 补一段标准回答 |

### 当前项目实现

项目本地默认使用 pgvector 作为向量库，leaf chunk 向量化后写入 PostgreSQL 的 vector 表；如果需要更大规模或独立向量服务，可以通过配置切换到 Milvus。检索阶段与 BM25 结果通过 RRF 融合。

### 下一步优化

| 优先级 | 任务 |
| --- | --- |
| P0 | 跑通检索消融评估，用数据回答 top_k 和 hybrid 是否有效 |
| P1 | 文档级 ACL metadata filter 接入检索阶段 |

## 5. 混合检索、BM25、RRF

### 常问问题

| 问题 | 当前项目能否回答 | 优化方向 |
| --- | --- | --- |
| 为什么需要 BM25？ | 能 | 精确词、编号、状态码、专有名词 |
| BM25 原理是什么？ | 能 | 需要会讲 tf、idf、文档长度归一化 |
| k1 和 b 是什么？ | 能 | 当前已配置化 |
| Dense 和 BM25 怎么融合？ | 能 | RRF |
| RRF 公式是什么？ | 能 | 需要背熟公式 |
| 为什么不用加权相加？ | 能 | 分数不可比，权重难调 |
| BM25 状态怎么持久化？ | 能 | 已实现状态级持久化 |
| 删除文档后 BM25 统计怎么同步？ | 能 | increment_remove |

### 当前项目实现

项目已经升级为状态级 BM25 持久化：

```text
BM25State
  -> vocab
  -> doc_freq
  -> total_docs
  -> sum_token_len / avg_doc_len
  -> documents[chunk_id].term_freq

新增文档:
  -> increment_add

删除 / 覆盖文档:
  -> 从 PostgreSQL 拉取旧 leaf chunk
  -> increment_remove
```

### 下一步优化

| 优先级 | 任务 |
| --- | --- |
| P0 | 用检索评估报告对比 dense、BM25、hybrid、hybrid + rerank |
| P1 | 对 BM25 k1、b 做参数扫描 |

## 6. Rerank

### 常问问题

| 问题 | 当前项目能否回答 | 优化方向 |
| --- | --- | --- |
| Rerank 是什么？ | 能 | 二阶段排序 |
| Bi-encoder 和 Cross-encoder 区别？ | 能 | 需要准备标准回答 |
| 为什么先召回再 rerank？ | 能 | 召回重覆盖，rerank 重精度 |
| candidate_k / top_n 怎么选？ | 部分能 | 需要评估支撑 |
| Rerank 很慢怎么优化？ | 能 | 条件触发、超时降级、缓存 |
| 什么情况下跳过 rerank？ | 能 | 候选不足、简单问题、超时 |

### 当前项目实现

当前项目已具备：

```text
Hybrid Retrieval
  -> DashScope gte-rerank
  -> rerank timeout
  -> candidate_count_below_threshold skip
  -> Retrieval Cache 命中时跳过 rerank
```

### 下一步优化

| 优先级 | 任务 |
| --- | --- |
| P0 | 在评估报告中记录 rerank 前后 MRR 和 latency |
| P1 | 根据 query complexity 和 score gap 动态决定是否 rerank |

## 7. Agentic RAG

### 常问问题

| 问题 | 当前项目能否回答 | 优化方向 |
| --- | --- | --- |
| Query Planning 是什么？ | 能 | AgentController / rag_strategy |
| Router 做了什么？ | 能 | 意图、复杂度、策略 |
| HyDE 是什么，什么时候用？ | 能 | 复杂或语义表达弱的问题 |
| Step-back 是什么？ | 能 | 抽象背景问题 |
| 子问题拆解怎么做？ | 能 | 复杂问题拆成多个检索 query |
| 多跳检索和普通多次检索有什么区别？ | 能 | 是否有依赖关系 |
| Rewrite 是前置做还是失败后做？ | 能 | 当前按策略和质量判断触发 |
| Agentic RAG 是不是把 RAG 包成工具？ | 能 | 这是其中一种实现，不是全部 |
| ReAct / Plan-and-Execute 和 Agentic RAG 关系？ | 需要准备 | 写标准回答 |

### 当前项目实现

```text
AgentController
  -> mode
  -> route
  -> memory_policy
  -> tool_plan
  -> rag_strategy
  -> need_grounding
  -> max_retries

RAG Workflow
  -> decomposition
  -> HyDE
  -> Step-back
  -> retrieval
  -> quality check
  -> rewrite / retry
  -> intermediate synthesis
  -> final generation
```

### 下一步优化

| 优先级 | 任务 |
| --- | --- |
| P0 | 写一份 Agentic RAG 分层和项目对应关系文档 |
| P1 | 把 Query Planning 输出结构在前端 Trace 展示得更清晰 |

## 8. 生成、幻觉控制与 Grounding

### 常问问题

| 问题 | 当前项目能否回答 | 优化方向 |
| --- | --- | --- |
| 怎么降低幻觉？ | 能 | 检索质量、prompt 约束、grounding |
| 如果知识库没有答案怎么办？ | 部分能 | 需要更明确拒答机制 |
| 如何判断召回结果质量？ | 能 | quality check |
| Grounding / Faithfulness 怎么做？ | 能 | claim-level verifier |
| 引用来源怎么生成？ | 能 | SourceNodePayload / citations |
| 答案和来源不一致怎么办？ | 部分能 | 需要强校验和修正 |
| 多文档内容冲突怎么办？ | 未完整实现 | 后续做冲突检测 |

### 当前项目实现

当前已有 optional grounding，但还不是强制闭环。

### 下一步优化

| 优先级 | 任务 |
| --- | --- |
| P0 | 无答案拒答机制：低质量召回时不生成或触发反问 |
| P0 | Context Packing：去重、分组、token budget、来源保留 |
| P1 | Claim-level grounding 失败后自动修正答案 |
| P1 | 多文档冲突检测 |

## 9. 记忆系统

### 常问问题

| 问题 | 当前项目能否回答 | 优化方向 |
| --- | --- | --- |
| RAG 系统需要记忆吗？ | 能 | 会话上下文和个性化 |
| 短期记忆和长期记忆区别？ | 能 | Redis window vs Mem0 |
| 滑动窗口怎么滑？ | 能 | 最近 N 条 |
| 摘要压缩什么时候触发？ | 能 | 超阈值后台压缩 |
| Redis、PostgreSQL、SessionSummary 分别存什么？ | 能 | 三层记忆 |
| 为什么不每次塞完整历史？ | 能 | token 和噪声控制 |
| Mem0 和 summary memory 区别？ | 能 | 语义事实 vs 会话摘要 |

### 当前项目实现

```text
PostgreSQL
  -> 完整历史

Redis
  -> 最近窗口缓存

SessionSummary
  -> 滚动摘要压缩

Mem0 Adapter
  -> 可选长期语义记忆
```

### 下一步优化

| 优先级 | 任务 |
| --- | --- |
| P1 | 补充记忆系统面试标准回答 |
| P2 | Mem0 长期语义记忆完整闭环 |

## 10. 性能优化与缓存

### 常问问题

| 问题 | 当前项目能否回答 | 优化方向 |
| --- | --- | --- |
| 为什么系统慢？ | 能 | 看 Trace 分阶段耗时 |
| 每个阶段耗时怎么定位？ | 能 | Performance 面板 |
| Retrieval Cache 缓存了什么？ | 能 | 最终检索节点 |
| 为什么不缓存最终答案？ | 能 | 答案受记忆、身份、模式影响 |
| Redis cache key 怎么设计？ | 能 | query + kb_version + retrieval config |
| 文档更新后缓存怎么失效？ | 能 | kb_version 指纹变化 |
| SSE 为什么不用 WebSocket？ | 能 | 单向流式输出，简单稳定 |
| Rerank 超时怎么降级？ | 能 | timeout fallback |

### 当前项目实现

```text
Retrieval Cache
  -> Redis
  -> key = normalized_query + kb_version + retrieval_config
  -> value = final source nodes
  -> TTL = RETRIEVAL_CACHE_TTL
```

### 下一步优化

| 优先级 | 任务 |
| --- | --- |
| P0 | 把缓存命中率和节省耗时写入 Trace |
| P1 | Semantic Cache：相似问题命中，需要阈值和误命中控制 |
| P1 | Query embedding cache |

## 11. 权限与安全

### 常问问题

| 问题 | 当前项目能否回答 | 优化方向 |
| --- | --- | --- |
| JWT / RBAC 怎么设计？ | 能 | admin/user |
| 文档级 ACL 怎么做？ | 规划能讲 | 需要落地 metadata filter |
| 权限过滤在检索前还是检索后？ | 能 | 主流是检索前 |
| 如果缓存里有越权内容怎么办？ | 能指出风险 | cache key 需要加权限维度 |
| Prompt Injection 怎么防？ | 部分能 | 需要 guardrails |
| 用户上传恶意文档怎么办？ | 部分能 | 需要文档安全扫描 |

### 当前项目实现

当前已有 JWT 和 RBAC，但文档级 ACL 过滤还不是完整企业级实现。

### 下一步优化

| 优先级 | 任务 |
| --- | --- |
| P0 | 文档级 ACL metadata schema |
| P0 | 检索阶段 metadata filter |
| P0 | Retrieval Cache key 加 tenant / role / permission_scope |
| P1 | Prompt Injection 检测和防护 |

## 12. 评估体系

### 常问问题

| 问题 | 当前项目能否回答 | 优化方向 |
| --- | --- | --- |
| RAG 怎么评估？ | 能 | 检索 + 生成分层评估 |
| 检索评估看什么指标？ | 能 | Hit@K、Recall@K、MRR |
| 生成评估看什么指标？ | 能 | Faithfulness、Answer Relevance |
| RAGAS 是什么？ | 能 | 需要准备标准回答 |
| 没有标准数据集怎么评估？ | 能 | 自建 QA + LLM 辅助标注 |
| 怎么证明 hybrid / rerank / BM25 有提升？ | 部分能 | 需要跑出稳定报告 |

### 当前项目实现

已有检索消融评估脚本：

```text
dense
bm25
hybrid
hybrid_rerank
```

指标：

```text
Hit@K
Recall@K
Precision@K
MRR
Latency
```

### 下一步优化

| 优先级 | 任务 |
| --- | --- |
| P0 | 正式跑一次检索评估，生成报告 |
| P0 | 把 BM25 状态持久化前后的效果和耗时写进报告 |
| P1 | RAGAS 生成层评估重新整理 |

## 13. 项目选型

### 常问问题

| 问题 | 当前项目能否回答 | 优化方向 |
| --- | --- | --- |
| 为什么用 FastAPI？ | 能 | 异步、SSE、Python AI 生态 |
| 为什么用 LlamaIndex？ | 能 | RAG 组件和索引生态 |
| 为什么不用 LangChain Agent？ | 能 | 项目更重 RAG workflow |
| 为什么本地默认 pgvector，同时保留 Milvus？ | 能 | 本地简化部署，生产保留扩展空间 |
| 为什么用 Redis？ | 能 | 会话窗口和检索缓存 |
| 为什么用 PostgreSQL？ | 能 | 用户、会话、parent chunk |
| 为什么用 SSE？ | 能 | 单向流式简单稳定 |

### 下一步优化

| 优先级 | 任务 |
| --- | --- |
| P1 | 整理一份技术选型标准回答 |

## 14. 当前推荐优化顺序

接下来建议按这个顺序推进：

| 顺序 | 优化任务 | 对应面试问题 |
| --- | --- | --- |
| 1 | 跑通检索评估闭环并生成报告 | 如何证明 hybrid / BM25 / rerank 有提升 |
| 2 | Context Packing + 无答案拒答 | 如何降低幻觉，知识库无答案怎么办 |
| 3 | 文档级 ACL 检索过滤 | 企业级权限怎么做，缓存越权怎么办 |
| 4 | Rerank 动态策略 | Rerank 慢怎么优化 |
| 5 | 表格 / 跨页表格解析策略 | 文档解析复杂场景怎么处理 |
| 6 | Grounding 失败后的自动修正 | 答案和证据不一致怎么办 |
| 7 | Semantic Cache | 相似问题如何复用检索结果 |
| 8 | GraphRAG / Web Search / MCP | 高级扩展，适合未来规划 |

## 15. 已形成的项目亮点

当前已经可以重点讲这些：

| 亮点 | 面试价值 |
| --- | --- |
| Hybrid Retrieval + RRF | 能解释为什么向量检索不够 |
| BM25 状态级持久化 | 能体现稀疏检索工程深度 |
| Parent-child chunk / Auto-merging | 能体现 chunk 策略不是简单切块 |
| AgentController | 能解释为什么是 Agentic RAG |
| Query Planning / HyDE / Step-back / Decomposition | 能处理复杂问题 |
| Retrieval Cache | 能解释性能优化和缓存失效 |
| 三层记忆系统 | 能解释上下文管理 |
| Trace / Performance 面板 | 能解释可观测和排错 |
| 检索评估脚本 | 能解释如何证明效果 |
