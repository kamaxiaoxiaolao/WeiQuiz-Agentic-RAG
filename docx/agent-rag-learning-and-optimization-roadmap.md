# Agent & RAG 学习与优化路线图

**文档版本**: v1.0  
**创建日期**: 2026-05-29  
**项目名称**: WeiQuiz (wei-quiz-rag)  
**文档目的**: 系统梳理 Agent 和 RAG 领域的学习要点，结合项目现状制定优化路线图

---

## 目录

1. [项目现状分析](#1-项目现状分析)
2. [高频面试问题与学习要点](#2-高频面试问题与学习要点)
3. [优化优先级路线图](#3-优化优先级路线图)
4. [具体优化任务清单](#4-具体优化任务清单)
5. [学习资源推荐](#5-学习资源推荐)
6. [面试表达参考](#6-面试表达参考)
7. [附录：关键概念速查](#7-附录关键概念速查)

---

## 1. 项目现状分析

### 1.1 已实现的核心能力

| 模块 | 实现状态 | 说明 |
|------|----------|------|
| **Query Router** | ✅ 已实现 | 规则优先 + LLM 兜底的意图分类 |
| **混合检索** | ✅ 已实现 | Milvus 向量检索 + BM25 稀疏检索 + RRF 融合 |
| **重排序** | ✅ 已实现 | DashScope gte-rerank 模型 |
| **Query Rewrite** | ✅ 已实现 | LLM 改写 + 规则兜底 |
| **质量评估** | ✅ 已实现 | top_k 分数 / 文本长度 / 来源多样性检查 |
| **层级分块** | ✅ 已实现 | HierarchicalNodeParser (root/parent/leaf 三级) |
| **Auto-Merging** | ✅ 已实现 | 子节点命中超阈值时自动合并为父节点 |
| **Agentic Workflow** | ✅ 已实现 | LlamaIndex Workflow 编排 |
| **SSE 流式** | ✅ 已实现 | 步骤级事件流推送 |
| **认证授权** | ✅ 已实现 | JWT + RBAC (user/admin) |
| **会话记忆** | ✅ 已实现 | Redis 持久化 ChatMemoryBuffer |

### 1.2 核心差距识别

基于《AgenticRAG差距分析报告》和《差距优先级报告》，当前系统与企业级主流 Agentic RAG 的主要差距：

| 维度 | 当前状态 | 企业级要求 | 差距等级 |
|------|----------|-----------|----------|
| **评估闭环** | 基础检索评估 | RAGAS 端到端评估 + 消融实验 | P0 |
| **可信生成** | 直接生成 | 子答案综合 + Grounding 检查 | P0 |
| **多跳推理** | Multi-query Retrieval | Evidence-aware Multi-hop | P1 |
| **工具调用** | 意图占位 | 真实工具闭环 + MCP | P1 |
| **安全防护** | 无 | Guardrails + Prompt Injection 防护 | P2 |
| **可观测性** | 前端 Trace | Langfuse/LangSmith 持久化 | P2 |
| **长期记忆** | Redis 短期记忆 | Mem0/Zep 长期语义记忆 | P3 |

---

## 2. 高频面试问题与学习要点

### 2.1 RAG 基础原理类

#### 问题 1：RAG 系统各环节如何排查问题？

**学习要点**：
- RAG 链路逐层定位：文档解析 → 文本清洗 → Chunk 切分 → Metadata → 向量/BM25 召回 → RRF 融合 → Rerank 精排 → 上下文拼接 → LLM 生成 → Grounding
- 核心原则：先判断正确证据有没有进入最终 prompt，再倒推问题发生在哪一层
- 常见排序：文档解析质量差 > chunk 切分不合理 > 召回没命中 > rerank 排错 > prompt/生成幻觉

**项目对应**：`docs/rag-troubleshooting-guide.md`

#### 问题 2：Dense vs BM25 vs Hybrid 怎么选？

**学习要点**：
- **Dense (向量检索)**：语义理解强，适合同义词、 paraphrase，但对精确关键词匹配弱
- **BM25 (稀疏检索)**：精确关键词匹配强，适合专业术语、代码、数字，但无语义理解
- **Hybrid (混合)**：结合两者优势，通过 RRF (Reciprocal Rank Fusion) 融合排序
- **消融实验**：需要分别测试 Dense only、BM25 only、Hybrid、Hybrid + Rerank 效果

**项目对应**：`app/rag_milvus.py` (混合检索实现)

#### 问题 3：为什么用父子块策略？切分粒度怎么定？

**学习要点**：
- **Leaf-only Indexing**：向量库只存 leaf chunk (256 chars)，小粒度精准匹配
- **Parent Context**：parent/root chunk (1024/2048 chars) 存 PostgreSQL，用于生成时回取完整上下文
- **Auto-merging**：同一 parent 下多个 leaf 被命中且超阈值时，自动回取 parent context
- **粒度选择**：leaf 要足够小以精准匹配，parent 要足够大以保留语义完整性

**项目对应**：`app/ingest/milvus_loader.py` (层级分块)、`app/retrieval/parent_context.py`、`app/retrieval/auto_merging_context.py`

### 2.2 检索优化类

#### 问题 4：为什么要加 Rerank？和 RRF 什么关系？

**学习要点**：
- **RRF (Reciprocal Rank Fusion)**：融合多路检索的排名，不比较分数绝对值，只看排名
- **Rerank (重排序)**：用 Cross-encoder 模型对候选集重新打分，精度更高但成本更大
- **关系**：RRF 是多路融合，Rerank 是精排，通常先 RRF 融合再 Rerank 精排
- **参数调优**：candidate_k (候选池大小)、final_top_k (最终返回数量)

**项目对应**：`app/rag_milvus.py` (QueryFusionRetriever + Rerank)

#### 问题 5：HyDE、Step-back、Sub-question 各自适用场景？

**学习要点**：
- **HyDE (Hypothetical Document Embeddings)**：先生成假设性答案，用其进行检索。适合问题模糊、需要语义扩展的场景
- **Step-back**：先问一个更上位的背景问题，获取背景信息后再回答原问题。适合需要先理解上下文的复杂问题
- **Sub-question Decomposition**：将复杂问题拆成多个子问题分别检索。适合多实体、多条件的复合问题
- **选择策略**：Router 根据问题复杂度和意图自动选择

**项目对应**：`app/agentic/hyde.py`、`app/agentic/step_back.py`、`app/agentic/sub_question.py`

### 2.3 Agent 架构类

#### 问题 6：Agent 和普通 RAG 有什么区别？

**学习要点**：
- **普通 RAG**：固定流程，用户查询 → 检索 → 生成
- **Agentic RAG**：动态决策，根据问题特征选择不同策略（直接回答、工具调用、多跳推理等）
- **核心组件**：Router（意图分类）、Planner（任务规划）、Executor（执行检索/工具）、Verifier（质量验证）
- **控制循环**：支持重试、策略切换、多步推理

**项目对应**：`app/agentic/controller.py` (AgentController 决策层)、`app/agentic/router.py` (Query Router)

#### 问题 7：多跳推理 (Multi-hop) 和多查询检索 (Multi-query) 有什么区别？

**学习要点**：
- **Multi-query Retrieval**：将一个问题改写成多个 query，分别检索后合并结果。各 query 之间独立
- **Multi-hop Reasoning**：第一跳检索结果影响第二跳问题生成，形成推理链
- **Evidence-aware**：每一跳后判断证据是否足够，动态生成下一跳问题
- **项目现状**：当前更接近 Multi-query，需要升级为 Evidence-aware Multi-hop

**项目对应**：`app/agentic/sub_question.py` (当前实现)、需要升级为 evidence-aware 版本

### 2.4 幻觉控制类

#### 问题 8：怎么保证答案忠实于文档？

**学习要点**：
- **Prompt 约束**：明确要求"仅基于上下文回答，信息不足时拒答"
- **Grounding 检查**：生成后将答案拆成 claim，逐条检查是否被 source nodes 支持
- **Faithfulness Score**：量化答案对源文档的忠实度，低于阈值触发重生成或拒答
- **引用精确性**：确保 `[来源 X]` 标注指向实际存在的相关内容

**项目对应**：`app/agentic/grounding.py` (Grounding 实现)

#### 问题 9：什么是 Self-RAG？和普通 RAG 有什么区别？

**学习要点**：
- **Self-RAG**：生成后自检"这个回答是否有源文档依据"，判断"是否需要补充检索"
- **闭环**：Generate → Reflect → Retrieve → Refine
- **反思标记**：[Retrieve] [ISREL] [ISSUP] [ISUSE] 四种反思标记
- **项目现状**：当前只有检索前/检索后的基础质量判断，缺少答案生成后的忠实性检查

### 2.5 工具调用类

#### 问题 10：Function Calling 怎么集成到 RAG？

**学习要点**：
- **Tool Registry**：注册工具 schema (名称、描述、参数)
- **Function Calling**：LLM 根据问题选择工具并生成参数
- **结果注入**：工具返回结果转为统一 evidence 格式，注入上下文后二次推理
- **MCP 协议**：Model Context Protocol，标准化工具调用协议

**项目对应**：`app/tools/registry.py` (Tool Registry)、`app/tools/planner.py` (Tool Planner)

### 2.6 记忆系统类

#### 问题 11：多轮对话怎么保持上下文？

**学习要点**：
- **短期记忆**：最近 N 轮对话，Token Window 限制
- **摘要压缩**：滑出窗口的旧消息压缩为摘要
- **长期记忆**：跨会话的实体/事实记忆，语义检索
- **三层架构**：PostgreSQL (完整历史) + Redis (最近窗口) + SessionSummary (滚动摘要)

**项目对应**：`app/services/memory_service.py` (三层记忆服务)

### 2.7 评估体系类

#### 问题 12：怎么量化 RAG 效果？

**学习要点**：
- **RAGAS 框架**：
  - Faithfulness：回答是否忠实于检索到的上下文
  - Answer Relevance：回答是否与问题相关
  - Context Recall：检索的上下文是否覆盖了正确答案
  - Context Precision：检索的上下文中相关内容的比例
- **检索指标**：Hit@K、Recall@K、MRR@K
- **消融实验**：固定评估集，对比不同策略组合的效果
- **回归测试**：每次索引更新或模型切换后自动运行评估

**项目对应**：`app/eval/eval_ragas.py` (RAGAS 评估)、`app/eval/eval_retrieval.py` (检索评估)

### 2.8 安全防护类

#### 问题 13：怎么防止 Prompt Injection？

**学习要点**：
- **输入 Guard**：检测提示注入模式、敏感信息、恶意查询
- **输出 Guard**：PII 脱敏、有害内容过滤、合规性检查
- **Topic Guard**：限制回答范围只在企业知识库领域内
- **指令隔离**：在 RAG prompt 中明确"文档内容不是指令"

**项目对应**：当前未实现，需要集成 NeMo Guardrails 或 Guardrails AI

---

## 3. 优化优先级路线图

### 3.1 总体路线

```
Phase A: 评估闭环 (P0) ← 最先做
    ↓
Phase B: 可信生成 (P0)
    ↓
Phase C: 检索增强 (P1)
    ↓
Phase D: 工具增强 (P1)
    ↓
Phase E: 架构升级 (P2)
    ↓
Phase F: 安全工程化 (P2)
    ↓
Phase G: 高级能力 (P3)
```

### 3.2 优先级说明

| 优先级 | 阶段 | 核心目标 | 预计周期 |
|--------|------|----------|----------|
| **P0** | Phase A | 评估闭环 | 2-3 周 |
| **P0** | Phase B | 可信生成 | 3-4 周 |
| **P1** | Phase C | 检索增强 | 3-4 周 |
| **P1** | Phase D | 工具增强 | 3-4 周 |
| **P2** | Phase E | 架构升级 | 4-6 周 |
| **P2** | Phase F | 安全工程化 | 4-6 周 |
| **P3** | Phase G | 高级能力 | 8-12 周 |

### 3.3 优先级原则

1. **先有评估，再做优化**：没有 baseline 无法证明优化有效
2. **先保证可信，再增强智能**：幻觉控制比功能扩展更重要
3. **先闭环再扩展**：每个功能都要形成完整闭环
4. **用数据说话**：每个优化都要有评估指标支撑

---

## 4. 具体优化任务清单

### 4.1 Phase A：评估闭环（P0）

**目标**：建立稳定 baseline，后续所有优化都用指标证明收益

| 任务 | 具体内容 | 完成标准 | 对应文件 |
|------|----------|----------|----------|
| A1 | 固定 RAGAS 版本和评估命令 | 一条命令可复现评估 | `app/eval/eval_ragas.py` |
| A2 | 构建 10-50 条评估数据集 | 覆盖企业典型查询场景 | `data/eval/` |
| A3 | 跑通 baseline 并输出报告 | `ragas_scores.json` + `ragas_report.md` | `data/eval/ragas_report.md` |
| A4 | 消融实验：Dense/BM25/Hybrid/Rerank | 对比各组件贡献 | `data/eval/ablation_report.md` |
| A5 | 记录 baseline 指标 | faithfulness、context_precision、context_recall | `docx/eval-baseline.md` |

**关键指标**：
- faithfulness ≥ 0.7
- context_precision ≥ 0.6
- context_recall ≥ 0.6

### 4.2 Phase B：可信生成闭环（P0）

**目标**：从"检索后直接生成"升级为"证据归因、子答案综合、生成后校验"

| 任务 | 具体内容 | 完成标准 | 对应文件 |
|------|----------|----------|----------|
| B1 | 子问题绑定 source_nodes | 每个 sub_question 有对应证据 | `app/agentic/sub_question.py` |
| B2 | 生成 intermediate_answer | 每个子问题有中间答案 | `app/agentic/sub_question.py` |
| B3 | 最终答案综合 | 基于多个 intermediate_answers 生成 | `app/agentic/node_synthesizer.py` |
| B4 | answer grounding check | 生成后做 claim-level 检查 | `app/agentic/grounding.py` |
| B5 | 证据不足处理 | unsupported 比例过高时触发拒答/重试 | `app/agentic/llama_workflow.py` |
| B6 | 前端 Trace 展示 | 子问题、证据、中间答案可见 | `frontend/src/components/DebugPanel.vue` |

**关键指标**：
- RAGAS faithfulness 不下降
- context_recall 尽量提升

### 4.3 Phase C：检索增强闭环（P1）

**目标**：提升复杂语义问题和表达不一致问题的召回能力

| 任务 | 具体内容 | 完成标准 | 对应文件 |
|------|----------|----------|----------|
| C1 | 完善 HyDE 执行链路 | 可配置开关，有对比报告 | `app/agentic/hyde.py` |
| C2 | 完善 Step-back 执行链路 | 可配置开关，有对比报告 | `app/agentic/step_back.py` |
| C3 | 多策略结果融合 | 原始/rewrite/HyDE 结果统一融合 | `app/agentic/llama_workflow.py` |
| C4 | Dynamic top_k | 根据查询复杂度自适应调整 | `app/rag_milvus.py` |
| C5 | 消融评估报告 | 各策略前后对比 | `data/eval/retrieval_ablation.md` |

**关键指标**：
- Recall@5 提升 10%+
- HyDE 前后有可量化差异

### 4.4 Phase D：工具增强闭环（P1）

**目标**：当本地知识库证据不足时，引入外部补充召回通道

| 任务 | 具体内容 | 完成标准 | 对应文件 |
|------|----------|----------|----------|
| D1 | 实现 Web Search 工具 | MCP 或 API 集成 | `app/tools/web_search.py` |
| D2 | 工具结果统一 evidence 格式 | 与 KB evidence 融合 | `app/agentic/llama_workflow.py` |
| D3 | 工具调用进入 Trace | 前端可见工具调用过程 | `frontend/src/components/DebugPanel.vue` |
| D4 | 本地优先策略 | KB 证据充足时不触发工具 | `app/agentic/router.py` |

**关键指标**：
- 工具调用成功率 ≥ 90%
- 工具结果对答案质量有正向贡献

### 4.5 Phase E：Agentic 架构升级（P2）

**目标**：从固定 Workflow 升级为受控 Agentic Workflow

| 任务 | 具体内容 | 完成标准 | 对应文件 |
|------|----------|----------|----------|
| E1 | 抽象 PlanStep | 定义任务步骤结构 | `app/agentic/planner.py` |
| E2 | 拆分 Planner/Executor/Verifier | 职责清晰分离 | `app/agentic/` |
| E3 | 受控 Agent Loop | max_steps、状态管理、失败恢复 | `app/agentic/llama_workflow.py` |
| E4 | 动态决策 | 复杂问题走 loop，简单问题走 pipeline | `app/agentic/controller.py` |

### 4.6 Phase F：企业安全与工程化（P2）

**目标**：让系统从"可演示"升级为"可解释、可管控、可上线"

| 任务 | 具体内容 | 完成标准 | 对应文件 |
|------|----------|----------|----------|
| F1 | 文档级 ACL | 文档 metadata 写入权限信息 | `app/ingest/milvus_loader.py` |
| F2 | 检索权限过滤 | Milvus 检索带 metadata filter | `app/rag_milvus.py` |
| F3 | Prompt Injection 防护 | 输入检测 + 指令隔离 | `app/api.py` |
| F4 | 会话锁/限流 | 防止并发冲突和滥用 | `app/api.py` |
| F5 | Trace 持久化 | 接入 Langfuse 或落库保存 | `app/agentic/llama_workflow.py` |

### 4.7 Phase G：高级增强能力（P3）

**目标**：在主链路稳定后补充高阶能力

| 任务 | 具体内容 | 完成标准 | 对应文件 |
|------|----------|----------|----------|
| G1 | 长期记忆系统 | Mem0/Zep 集成，跨会话记忆 | `app/services/long_term_memory_service.py` |
| G2 | GraphRAG | 实体抽取、关系抽取、社区摘要 | 新模块 |
| G3 | Multi-agent | 工具拆分给专业化 agent | 新模块 |

---

## 5. 学习资源推荐

### 5.1 RAG 基础原理

| 资源 | 类型 | 说明 |
|------|------|------|
| LlamaIndex 官方文档 | 文档 | RAG 各组件详细实现 |
| LangChain RAG 教程 | 教程 | 多种 RAG 模式示例 |
| 《Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks》 | 论文 | RAG 原始论文 |

### 5.2 Agentic RAG

| 资源 | 类型 | 说明 |
|------|------|------|
| 《Self-RAG: Learning to Retrieve, Generate, and Critique》 | 论文 | Self-RAG 原理 |
| 《Corrective Retrieval Augmented Generation》 | 论文 | CRAG 原理 |
| 《Adaptive-RAG: Learning to Adapt Retrieval-Augmented Large Language Models》 | 论文 | Adaptive RAG |
| LlamaIndex Workflows | 文档 | Agentic Workflow 实现 |

### 5.3 RAG 评估

| 资源 | 类型 | 说明 |
|------|------|------|
| RAGAS 官方文档 | 文档 | Faithfulness/Context Precision/Recall/Answer Relevancy |
| DeepEval | 框架 | 端到端 RAG 评估 |
| LlamaIndex Evaluation | 文档 | 内置评估工具 |

### 5.4 工具调用

| 资源 | 类型 | 说明 |
|------|------|------|
| OpenAI Function Calling | 文档 | Function Calling 规范 |
| MCP 协议 | 规范 | Model Context Protocol |
| LangChain Tools | 文档 | 工具集成示例 |

### 5.5 可观测性

| 资源 | 类型 | 说明 |
|------|------|------|
| Langfuse | 平台 | 开源 LLM 可观测性 |
| LangSmith | 平台 | LLM 应用追踪和评估 |
| Arize Phoenix | 平台 | 检索和生成质量分析 |

### 5.6 安全防护

| 资源 | 类型 | 说明 |
|------|------|------|
| NeMo Guardrails | 框架 | NVIDIA 开源 Guardrails |
| Guardrails AI | 框架 | 输入输出安全防护 |
| OWASP LLM Top 10 | 标准 | LLM 应用安全风险 |

### 5.7 知识图谱

| 资源 | 类型 | 说明 |
|------|------|------|
| Neo4j | 数据库 | 图数据库 |
| Microsoft GraphRAG | 框架 | 知识图谱增强 RAG |
| LlamaIndex KnowledgeGraph | 文档 | 知识图谱索引 |

---

## 6. 面试表达参考

### 6.1 项目介绍

> 这是一个面向企业知识库问答的 Agentic RAG 系统。核心架构是 Query Router + Hybrid Retrieval + Rerank + Agentic Workflow。Router 用规则优先 + LLM 兜底判断意图，复杂问题会进入子问题分解和多路检索，检索结果经过 RRF 融合和 Rerank 精排后生成答案。系统支持 SSE 流式输出和完整的 Trace 展示。

### 6.2 问题排查

> 如果用户反馈答案不准，我会按 RAG 链路倒查。第一步先看最终 prompt 或 source nodes 里有没有正确证据。如果有正确证据但模型答错，说明是生成层或 prompt 约束问题。如果最终上下文里没有正确证据，我会继续看 rerank 前的候选集；如果候选里有但 rerank 后没进 top_k，说明是 rerank 或 top_k 问题。如果候选里也没有，就分别看 dense、BM25、hybrid 的召回结果。我们项目里有 trace、source nodes、ingestion report 和 audit markdown，所以可以比较系统地定位问题。

### 6.3 项目差距

> 当前系统已经实现了 Agentic RAG 的基础闭环，包括 Query Router、Hybrid Retrieval、Rerank、Quality Check、Query Rewrite、Sub-question Decomposition。但距离成熟 Agentic RAG 还有几个差距：第一，子问题之间还没有 evidence-aware 的迭代依赖；第二，还没有先生成子答案再综合的 decompose-and-synthesize；第三，缺少生成后的 grounding / faithfulness 检查；第四，评估体系还主要停留在检索评估，没有端到端 RAG 评估。所以后续我会优先做三个方向：RAGAS baseline → 子答案综合 + Grounding → 消融实验证明收益。

### 6.4 优化思路

> RAG 优化不能只凭感觉，要用数据说话。我会先建立稳定 baseline，用 RAGAS 框架量化 faithfulness、context_precision、context_recall。然后对 Dense、BM25、Hybrid、Rerank 做消融实验，证明每个组件的贡献。之后再做子答案综合和 Grounding 检查，每个优化都有评估指标支撑。

---

## 7. 附录：关键概念速查

### 7.1 检索相关

| 概念 | 说明 |
|------|------|
| **Dense Retrieval** | 基于向量嵌入的语义检索 |
| **Sparse Retrieval (BM25)** | 基于词频的关键词检索 |
| **Hybrid Retrieval** | 结合 Dense 和 Sparse 的混合检索 |
| **RRF (Reciprocal Rank Fusion)** | 多路检索排名融合算法 |
| **Rerank** | 用 Cross-encoder 对候选集重新排序 |
| **HyDE** | 先生成假设性文档再进行检索 |
| **Step-back** | 先问上位背景问题获取上下文 |
| **Multi-hop** | 多跳推理，第一跳结果影响第二跳 |
| **Multi-query** | 多查询检索，各 query 独立 |

### 7.2 Chunk 相关

| 概念 | 说明 |
|------|------|
| **Chunk** | 文档切分后的片段 |
| **Leaf Chunk** | 最小粒度切分，用于向量检索 |
| **Parent Chunk** | 较大粒度，用于生成时回取上下文 |
| **Root Chunk** | 最大粒度，保留完整语义 |
| **Auto-merging** | 子节点命中超阈值时自动合并为父节点 |
| **Hierarchical Chunk** | 多层级分块策略 |

### 7.3 Agent 相关

| 概念 | 说明 |
|------|------|
| **Agentic RAG** | 具备动态决策能力的 RAG 系统 |
| **Router** | 意图分类，决定走哪条链路 |
| **Planner** | 任务规划，决定执行步骤 |
| **Executor** | 执行检索、工具调用等 |
| **Verifier** | 质量验证，决定是否重试 |
| **ReAct** | Reasoning + Acting 交替执行模式 |
| **Plan-and-Execute** | 先规划再执行模式 |
| **Tool Calling** | LLM 调用外部工具 |
| **MCP** | Model Context Protocol，工具调用协议 |

### 7.4 评估相关

| 概念 | 说明 |
|------|------|
| **RAGAS** | RAG 评估框架 |
| **Faithfulness** | 回答是否忠实于检索到的上下文 |
| **Answer Relevance** | 回答是否与问题相关 |
| **Context Recall** | 检索的上下文是否覆盖了正确答案 |
| **Context Precision** | 检索的上下文中相关内容的比例 |
| **Hit@K** | 前 K 个结果中是否包含正确答案 |
| **Recall@K** | 前 K 个结果覆盖了多少正确答案 |
| **MRR (Mean Reciprocal Rank)** | 正确答案排名的倒数的平均值 |
| **消融实验** | 逐个移除组件以验证各组件贡献 |

### 7.5 幻觉控制相关

| 概念 | 说明 |
|------|------|
| **Grounding** | 答案依据校验，检查关键结论是否被证据支持 |
| **Faithfulness Score** | 量化答案对源文档的忠实度 |
| **Claim-level Check** | 将答案拆成声明逐条检查 |
| **Self-RAG** | 生成后自检并可触发重检索 |
| **CRAG** | Corrective RAG，检索不佳时自动切换策略 |
| **Guardrails** | 输入输出安全防护框架 |
| **Prompt Injection** | 通过输入诱导模型忽略系统规则 |

### 7.6 记忆相关

| 概念 | 说明 |
|------|------|
| **Short-term Memory** | 最近 N 轮对话，Token Window 限制 |
| **Summary Memory** | 滑出窗口的旧消息压缩为摘要 |
| **Long-term Memory** | 跨会话的实体/事实记忆，语义检索 |
| **Mem0** | 长期语义记忆框架 |
| **Zep** | 长期记忆服务 |
| **User Profile** | 基于历史交互的用户画像 |

---

**文档维护**：本文档应随项目迭代定期更新，跟踪学习进度和优化进展。

**相关文档**：
- `docs/rag-troubleshooting-guide.md` - RAG 故障排查手册
- `docx/AgenticRAG差距分析报告.md` - 差距详细分析
- `docx/agentic-rag-gap-priority-report.md` - 优先级路线图
- `docx/RAG 系统评估计划书.md` - 评估计划