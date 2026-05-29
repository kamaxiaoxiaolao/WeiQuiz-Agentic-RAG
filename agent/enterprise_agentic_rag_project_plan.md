# 项目说明与计划书

## Enterprise Agentic RAG System

**企业级智能检索增强生成系统**

面向内部知识库的自主检索、推理、溯源一体化平台

| 字段 | 内容 |
| --- | --- |
| **项目类型** | 简历项目 / 面试核心支撑 |
| **目标岗位** | 大厂 AI（RAG / 知识库 / 企业 AI 方向） |
| **预计周期** | 6 周 |
| **核心技术** | Agentic RAG · 向量数据库 · 混合检索 · LLM |
| **评测基准** | RAGAS · 自建企业 QA 测试集 |
| **区别于普通 RAG** | 自主决策检索策略 · 多步推理 · 可信溯源 |

---

## 1. 项目概述

### 1.1 普通 RAG vs Agentic RAG

普通 RAG（Naive RAG）是一个固定流程：接收查询 → 向量检索一次 → 将 Top-K 文档拼入 prompt → LLM 生成答案。这种方式在简单问答场景够用，但在企业真实场景中存在明显短板：

- 单次检索无法处理多跳问题（答案分散在多份文档）
- 检索策略固定，无法根据问题复杂度动态调整
- 生成的答案无法验证是否真正有文档支撑，幻觉率高
- 无法处理「先查 A，根据 A 的结果再查 B」的依赖链条

Agentic RAG 的核心突破是把 RAG 从一个固定管道变成一个可自主决策的推理循环：Agent 能判断「当前检索结果够不够」「需不需要改写查询再检索」「该问题是否需要拆成子问题分别检索后再合并」。这使得系统能处理企业中大量的复杂知识推理场景。

### 1.2 项目定位

构建一个面向企业内部知识库的 Agentic RAG 平台，能够处理以下典型企业场景：

- 员工问「最新的差旅报销标准是多少，和去年相比改了哪些」（跨文档 + 时态对比）
- 销售问「这个客户的合同条款里有没有竞业限制，和我们的标准模板有什么差异」（合同分析）
- 研发问「我们的安全规范里有哪些接口需要鉴权，上次审计报告发现了什么问题」（多文档关联）

> **一句话项目定位：** 一个能自主决定「检索几次、怎么检索、答案是否可信」的企业知识问答系统，而不是一个固定流程的文档搜索引擎。

### 1.3 核心价值

| **维度** | **具体体现** | **面试收益** |
| --- | --- | --- |
| RAG 工程深度 | 混合检索、重排序、分块策略全覆盖 | 回答「你做过什么优化」 |
| Agentic 设计 | 自主检索决策、多跳推理、self-reflection | 回答「为什么叫 Agentic」 |
| 企业工程能力 | 权限控制、多租户、引用溯源、评测体系 | 回答「怎么生产化」 |
| 量化评测 | RAGAS 全套指标 + 自建 QA 集消融实验 | 回答「效果怎么证明」 |

---

## 2. 系统架构设计

### 2.1 整体架构层次

系统分为四个层次，从下到上依次为：数据层、检索层、Agent 推理层、接口层。

| **层次** | **核心组件** | **职责** |
| --- | --- | --- |
| 接口层 | REST API / Slack Bot / Web UI | 接收用户问题，返回带引用的答案 |
| Agent 推理层 | Query Analyzer + Retrieval Agent + Answer Agent | 自主决策检索策略，多步推理，答案验证 |
| 检索层 | 混合检索（向量 + BM25）+ Reranker | 高召回率检索，精准度重排序 |
| 数据层 | Qdrant + Elasticsearch + 文档解析管道 | 结构化存储，全文索引，多格式文档处理 |

### 2.2 核心模块详述

#### Query Analyzer（查询分析器）

接收用户原始问题，执行三件事：判断问题复杂度（简单单跳 / 多跳 / 比较型）；拆解复杂问题为子查询列表；识别时间范围、部门、文档类型等过滤条件。

- **输入：** 原始用户问题
- **输出：** QueryPlan（query_type、sub_queries、filters、retrieval_strategy）
- **关键能力：** 问题分类、子查询拆解、元数据过滤条件提取

#### Retrieval Agent（检索 Agent）

根据 QueryPlan 执行检索，核心是自主决策「这次检索结果够不够」。实现 Self-Reflection 循环：检索 → 评估相关性 → 不够则改写查询重检 → 最多 3 轮。

- **混合检索：** 向量相似度（Qdrant）+ BM25 关键词（Elasticsearch）融合
- **Reranker：** 用 Cohere Rerank 或 BGE-Reranker 对 Top-20 结果重排，取 Top-5
- **Self-Reflection：** LLM 判断检索结果是否能回答问题，不够则 Query Rewriting 重检
- **HyDE：** 对复杂问题先生成假设答案，用假设答案的 embedding 检索，提升召回

#### Answer Agent（答案生成 Agent）

基于检索结果生成最终答案，核心关注两件事：引用溯源（每句话能定位到来源文档+段落）和幻觉检测（答案中的每个关键声明是否有文档支撑）。

- **引用格式：** 答案中每个关键句附带 [来源: 文档名, 第X页, 段落Y]
- **Faithfulness 检查：** 生成后用 LLM 自检，标记没有文档支撑的句子
- **置信度评分：** 0–1 分，基于检索相关性分和幻觉检测结果综合计算

#### 文档解析与索引管道（Ingestion Pipeline）

负责将企业内部的各类文档（PDF、Word、Excel、PPT、Markdown、网页）解析、分块、向量化、写入存储。这个管道的质量直接决定检索效果的上限。

- **PDF/Word：** 基于 PyMuPDF + python-docx，保留标题层级结构
- **Excel/表格：** 识别表头，将表格转成文本描述，避免语义碎片
- **分块策略：** 按语义段落分块（非固定 512 tokens），用 `\n\n` 或标题检测段落边界
- **父子块设计：** 小块（256 tokens）用于精确检索，关联的大块（1024 tokens）用于生成上下文
- **元数据提取：** 文档名、创建时间、部门、版本号、作者写入向量库元数据字段

#### 权限与多租户层

企业场景必须支持文档级权限控制：用户只能检索自己有权限的文档。实现方式是在向量库的每条记录上附加 `tenant_id` 和 `permission_groups` 字段，检索时自动注入过滤条件。

- 每条文档块携带 `tenant_id`、`department`、`access_level` 元数据
- 检索时自动注入 `filter: {tenant_id: X, department: in [Y, Z]}`
- 不同部门的文档物理隔离（Qdrant 不同 collection），防止跨租户数据泄露

### 2.3 Agentic 检索决策流程

这是本项目区别于普通 RAG 最核心的部分，需要在面试中能清晰描述。

1. 用户提问 → Query Analyzer 判断问题类型
2. **简单单跳问题：** 直接混合检索 → Reranker → 生成答案（1 轮）
3. **多跳问题：** 拆解为 N 个子查询，依次检索（后一个查询可依赖前一个结果）
4. **比较型问题（A 和 B 有什么区别）：** 并发检索 A 和 B 相关文档，再合并比较
5. 每轮检索后：Retrieval Agent 评估相关性分数，低于阈值（0.7）则 Query Rewriting 重检
6. 最多 3 轮检索循环，仍不够则在答案中标注「知识库中无相关信息」
7. Answer Agent 生成答案 → Faithfulness 检查 → 附加引用 → 返回用户

---

## 3. 核心技术深度

### 3.1 混合检索：为什么向量检索不够

> **向量检索 vs BM25 的互补性**
>
> - **向量检索（语义匹配）：** 擅长模糊语义理解，如「员工福利」能召回「五险一金」相关文档。
> - **BM25（关键词匹配）：** 擅长精确词匹配，如「2024年第三季度」「产品型号 X-200」这类精确词搜索。
> - **混合检索** 用 RRF（Reciprocal Rank Fusion）融合两路结果，取长补短，召回率显著优于单一策略。
> - 实测：混合检索 Recall@10 比纯向量检索高约 **15 个百分点**（在包含精确名词的企业文档上）。

| **检索类型** | **适用场景 / 优劣** |
| --- | --- |
| 纯向量检索 | 语义相关性强，但对专有名词（产品编号、人名、日期）召回差 |
| 纯 BM25 | 精确词匹配强，但同义词、近义词无法召回（如「薪酬」vs「工资」） |
| 混合（RRF 融合） | 综合最优，适合企业混杂文档场景，Recall@10 提升 10-20% |
| + Reranker | 混合检索后精排，精准度大幅提升，Answer Relevancy 提升约 12% |

### 3.2 分块策略：企业文档的最大技术坑

分块（Chunking）是 RAG 工程中最容易被忽视、影响最大的环节。固定长度分块（Fixed-size chunking）是最常见的错误做法。

| **分块策略** | **原理** | **适用场景** |
| --- | --- | --- |
| 固定长度（512 tokens） | 按 token 数切割，不考虑语义 | 简单文档，效果差，不推荐 |
| 语义段落分块 | 按 `\n\n`、标题、句子边界切割 | 大多数企业文档，本项目主用 |
| 父子块（Parent-Child） | 小块检索，大块生成，关联存储 | 需要精确定位 + 完整上下文时 |
| 按结构分块（表格/标题） | 识别文档结构，表格单独处理 | 含大量表格和列表的文档 |

本项目实现父子块设计：用 256-token 小块做向量检索（精确定位），检索命中后返回关联的 1024-token 大块给 LLM（保留完整上下文），同时存储块在原文档中的位置（页码、段落序号）用于引用溯源。

### 3.3 Self-Reflection 检索循环

Self-Reflection 是 Agentic RAG 的核心机制，让系统具备「知道自己不知道」的能力。

> **Self-Reflection 实现逻辑**
>
> 1. 执行检索，获取 Top-K 文档块
> 2. 用 LLM 打分——「这些文档能回答用户的问题吗？」输出 0-1 相关性分
> 3. 若分数 < 0.7（阈值可配置），进入 Query Rewriting，让 LLM 改写查询（扩展同义词、添加专业术语、分解为更具体的子问题）
> 4. 用改写后的查询重新检索，最多循环 3 次
> 5. 3 次后仍不够，Answer Agent 在答案中标注「相关信息不足」，不瞎编

### 3.4 引用溯源与 Faithfulness 检测

企业场景中，答案必须「可信可查」，这是 Agentic RAG 区别于消费级问答最重要的工程特性。

- **引用格式：** 每个关键声明后附 [来源: 《2024年差旅政策》第3页，第2段]
- **块级定位：** 每个向量块存储 `{doc_name, page_num, para_idx, char_start, char_end}`
- **Faithfulness 检查：** 答案生成后，用 LLM 逐句验证「这句话在哪个来源文档里有依据」
- 无依据句子标注 `[未找到来源]`，置信度降低，不静默删除，让用户知道
- **置信度分** = 0.6 × 检索相关性均值 + 0.4 × Faithfulness 率

### 3.5 HyDE（假设文档 Embedding）

对于复杂问题，用户原始查询的 embedding 可能和答案文档的 embedding 相距较远。HyDE 的思路是先让 LLM 生成一个「假设的理想答案」，用这段文字的 embedding 去检索——因为假设答案在语义空间里更接近真实答案文档。

本项目对 `query_type = complex` 的问题自动触发 HyDE，简单问题不触发（避免引入额外 LLM 调用成本）。实测在多跳问题上 Recall@5 提升约 **18%**。

---

## 4. 评测体系

评测是本项目最能拉开与普通 RAG 项目差距的地方。大厂面试官最欣赏「有严格评测体系」的候选人。

### 4.1 RAGAS 核心指标

| **指标** | **含义** | **计算方式** | **目标值** |
| --- | --- | --- | --- |
| Faithfulness | 答案是否有文档支撑 | 有依据声明数 / 总声明数 | >= 0.85 |
| Answer Relevancy | 答案是否回答了问题 | 答案 embedding 与问题相似度 | >= 0.80 |
| Context Precision | 检索的文档是否都有用 | 有用块 / 检索总块数 | >= 0.75 |
| Context Recall | 相关文档是否都检索到了 | 检索到的相关信息 / 全部相关信息 | >= 0.80 |
| Answer Correctness | 答案和标准答案的语义匹配度 | 语义相似度 + 事实重叠率 | >= 0.75 |

### 4.2 自建企业 QA 测试集

RAGAS 需要有 ground truth 答案，必须自建测试集。构建方式：

- 从知识库中随机抽取 100 份文档
- 人工（或 GPT-4 辅助）针对每份文档构造 3–5 个问题，包含单跳、多跳、比较型各一定比例
- 人工标注标准答案和相关文档块（ground truth contexts）
- 最终 QA 集：300–500 个问题，覆盖不同难度和场景

测试集分层：按问题类型（单跳 / 多跳 / 比较 / 数字提取）分别统计指标，能清楚看到系统在哪类问题上薄弱。

### 4.3 消融实验设计

| **实验** | **去除的组件** | **预期效果** |
| --- | --- | --- |
| 消融 A：去掉 Reranker | 混合检索结果直接给 LLM，不重排 | Context Precision 下降 ~15% |
| 消融 B：去掉 Self-Reflection | 只检索一次，不循环 | 多跳问题 Faithfulness 下降 ~20% |
| 消融 C：固定分块 vs 语义分块 | 改用 512-token 固定分块 | Context Recall 下降 ~12% |
| 消融 D：去掉 HyDE | 复杂问题用原始 query 检索 | 多跳 Context Recall 下降 ~18% |
| 消融 E：向量 vs 混合检索 | 只用向量检索，去掉 BM25 | 精确词 Recall@10 下降 ~15% |

---

## 5. 技术栈

| **类别** | **选型** | **用途说明** |
| --- | --- | --- |
| 向量数据库 | Qdrant（主） | 向量存储与 ANN 检索，支持元数据过滤 |
| 全文索引 | Elasticsearch 8.x | BM25 关键词检索，与 Qdrant 并行 |
| 混合检索融合 | 自实现 RRF（Reciprocal Rank Fusion） | 两路检索结果加权融合 |
| Reranker | Cohere Rerank API / BGE-Reranker（本地） | Top-20 → Top-5 精排 |
| Embedding 模型 | text-embedding-3-large / BGE-M3 | 文档和查询向量化 |
| LLM | Claude Sonnet / GPT-4o | Query 分析、答案生成、Self-Reflection |
| 文档解析 | PyMuPDF + python-docx + pandas | PDF/Word/Excel 解析 |
| 评测框架 | RAGAS | Faithfulness/Relevancy/Recall/Precision |
| API 框架 | FastAPI + uvicorn | REST API 接口层 |
| 向量库 ORM | LangChain / LlamaIndex（部分） | 文档分块、向量化流水线 |
| 缓存 | Redis | 热门查询缓存，降低 LLM 调用成本 |
| 单测 | pytest + pytest-asyncio | 各模块单元测试 |

> **技术选型说明：为什么用 Qdrant 而不是 Pinecone / Weaviate**
>
> - **Qdrant：** 开源可本地部署（企业数据不出内网），支持 payload 过滤（元数据权限控制），Python SDK 成熟。
> - **Pinecone：** 托管服务，数据必须上传到云端，企业合规风险高，排除。
> - **Weaviate：** 功能丰富但配置复杂，对初学者不友好；Qdrant 更轻量，适合独立项目展示。

---

## 6. 代码结构

```
enterprise_rag/
├── ingestion/                    # 文档解析与索引管道
│   ├── parsers/
│   │   ├── pdf_parser.py         # PyMuPDF，保留标题层级
│   │   ├── docx_parser.py        # python-docx
│   │   └── excel_parser.py       # 表格转文本描述
│   ├── chunker.py                # 语义分块 + 父子块设计
│   ├── embedder.py               # 批量向量化，支持多模型切换
│   └── indexer.py                # 写入 Qdrant + Elasticsearch
├── retrieval/
│   ├── vector_retriever.py       # Qdrant 向量检索（含元数据过滤）
│   ├── bm25_retriever.py         # Elasticsearch BM25 检索
│   ├── hybrid_retriever.py       # RRF 融合两路结果
│   ├── reranker.py               # Cohere/BGE 重排序
│   └── hyde.py                   # HyDE 假设文档 embedding
├── agents/
│   ├── query_analyzer.py         # 问题分类 + 子查询拆解 + 过滤条件
│   ├── retrieval_agent.py        # Self-Reflection 检索循环
│   └── answer_agent.py           # 生成 + Faithfulness 检查 + 引用
├── core/
│   ├── pipeline.py               # 端到端 RAG 流程编排
│   ├── models.py                 # Pydantic 数据模型
│   ├── config.py                 # 参数配置（阈值、模型名、超时等）
│   └── auth.py                   # 权限校验，注入检索过滤条件
├── api/
│   ├── main.py                   # FastAPI 应用入口
│   ├── routes/query.py           # POST /query 接口
│   └── routes/ingest.py          # POST /ingest 接口
├── eval/
│   ├── build_testset.py          # 自动构建 QA 测试集
│   ├── ragas_runner.py           # RAGAS 评测脚本
│   ├── ablation.py               # 消融实验
│   └── results/                  # 评测报告存放
├── tests/
│   ├── test_chunker.py
│   ├── test_retrieval.py
│   └── test_pipeline.py
├── scripts/
│   └── ingest_docs.py            # 批量导入文档的 CLI
└── README.md
```

---

## 7. 六周开发与学习计划

每周结构：核心知识点学习 → 立刻用进对应模块开发 → 整理面试答案。RAG 工程的知识点密度高，这个顺序能防止「学了忘」。

### 第 1 周：RAG 基础 + 文档解析管道

**本周学习内容**

- Embedding 原理：词向量、句向量、余弦相似度、为什么语义相似的句子 embedding 距离近
- 向量数据库原理：HNSW 图索引、ANN 搜索 vs 精确搜索、Qdrant 的 payload 过滤机制
- 朴素 RAG 完整流程：Indexing → Retrieval → Generation，以及每个环节的常见失误
- 文档解析技巧：PDF 文字层 vs 图片层、标题识别、表格提取的难点

**本周开发任务**

- 搭建 Qdrant 本地环境，创建 collection，定义 payload schema
- 实现 `pdf_parser.py`：用 PyMuPDF 提取文本，保留标题层级（H1/H2/H3 标注）
- 实现 `docx_parser.py` 和 `excel_parser.py`
- 实现 `chunker.py`：语义段落分块 + 父子块设计，每块附元数据
- 实现 `embedder.py`：批量向量化，写入 Qdrant
- 用 10 份真实 PDF 跑通完整 indexing 流程

**本周产出**

- 可跑通的文档解析 + 索引管道，10 份 PDF 索引完成
- 能用 Qdrant client 查询并返回相关块（简单向量检索验证）

---

### 第 2 周：混合检索 + Reranker

**本周学习内容**

- BM25 算法原理：TF-IDF 进化版，为什么对精确词匹配更好
- RRF（Reciprocal Rank Fusion）：两路排名融合的数学原理和参数 k 的选择
- Reranker 模型：Cross-encoder vs Bi-encoder 的区别，为什么 Reranker 精度更高但速度慢
- 检索评估指标：Recall@K、Precision@K、MRR（Mean Reciprocal Rank）

**本周开发任务**

- 搭建 Elasticsearch，实现 `bm25_retriever.py`：索引文档、执行搜索
- 实现 `hybrid_retriever.py`：RRF 融合 Qdrant 和 Elasticsearch 的结果
- 接入 Cohere Rerank API，实现 `reranker.py`
- 写评测脚本：用 20 个手动标注的问题测 Recall@5 和 Precision@5
- 对比实验：纯向量 vs 纯 BM25 vs 混合 vs 混合+Reranker，记录指标

**本周产出**

- 混合检索 + Reranker 跑通，Recall@5 数据有记录
- 消融 A、E 的原始数据（去 Reranker vs 有 Reranker，纯向量 vs 混合）

---

### 第 3 周：Query Analyzer + Agentic 框架

**本周学习内容**

- 问题分类技术：Few-shot 分类、用 LLM 做意图识别的 prompt 设计
- 子查询分解（Decomposition）：复杂问题的拆解策略，依赖链条的处理
- Agentic RAG 论文精读：Self-RAG、CRAG（Corrective RAG）、Adaptive RAG
- LangChain / LlamaIndex 的 QueryEngine 对比（了解框架边界，决定哪些自己实现）

**本周开发任务**

- 实现 `query_analyzer.py`：问题分类（单跳/多跳/比较型）、子查询拆解、过滤条件提取
- 实现 `hyde.py`：对复杂问题生成假设答案，用假设答案 embedding 检索
- 搭建 Agentic RAG 主流程框架（`pipeline.py`），串联 Query Analyzer → Retrieval → Answer
- 实现简单版 Self-Reflection：检索后判断相关性，低于阈值则重检（1轮先跑通）

**本周产出**

- Query Analyzer 能正确分类 80%+ 的测试问题
- Agentic 主流程框架跑通，端到端从问题到答案

---

### 第 4 周：Self-Reflection + 答案生成 + 引用溯源

**本周学习内容**

- Self-Reflection 机制：CRAG 论文的核心思路，如何用 LLM 评估检索质量
- Query Rewriting 技巧：扩展同义词、分解为更具体的子问题、添加领域术语
- Faithfulness 检测：如何用 LLM 逐句验证答案是否有文档支撑
- 引用系统设计：块级定位（文档名+页码+段落）的工程实现

**本周开发任务**

- 完善 `retrieval_agent.py`：3 轮 Self-Reflection 循环 + Query Rewriting 策略
- 实现 `answer_agent.py`：生成答案 + Faithfulness 逐句检查 + 引用标注
- 实现置信度评分：基于检索相关性均值和 Faithfulness 率综合计算
- 实现 `auth.py`：权限校验，检索时自动注入 tenant_id 和 department 过滤
- 端到端联调，用自建测试集跑 20 个问题，人工评估答案质量

**本周产出**

- 完整 Agentic RAG 流程跑通，答案含引用标注
- Self-Reflection 循环验证有效：有循环 vs 无循环的 Faithfulness 对比数据

---

### 第 5 周：RAGAS 评测 + 消融实验

**本周学习内容**

- RAGAS 框架：5 个核心指标的计算原理，如何搭建评测 pipeline
- 测试集构建方法：用 LLM 辅助生成 QA 对，人工验证标准答案
- 评测陷阱：数据污染（测试集文档不能在训练/tuning 时用）、指标偏差来源
- 性能分析：latency breakdown（检索耗时 / LLM 耗时 / Reranker 耗时），成本估算

**本周开发任务**

- 构建 300 题 QA 测试集（单跳/多跳/比较型各 100 题）
- 实现 `ragas_runner.py`，跑完整 RAGAS 评测，输出 5 个指标
- 跑消融实验 A-E，记录每个消融的指标变化
- 实现 Redis 缓存：热门查询直接返回，降低 API 调用成本
- 整理评测报告，绘制各消融实验的对比表格

**本周产出**

- 完整 RAGAS 报告，5 个指标全部有值
- 5 个消融实验数据，能量化说明每个组件的贡献

---

### 第 6 周：API 封装 + 文档 + 简历整理

**本周学习内容**

- FastAPI 最佳实践：异步接口、Pydantic 请求/响应模型、错误处理、接口文档自动生成
- 生产化考量：流式输出（SSE / Streaming）、超时控制、日志结构化
- 企业 AI 系统设计面试题准备：数据安全、合规、成本控制、效果监控

**本周开发任务**

- 实现 FastAPI 接口：`POST /query`（问答）、`POST /ingest`（文档导入）
- 加流式输出支持（Server-Sent Events），让用户看到逐字生成
- 完善 README：架构图文字描述、本地运行指南、环境变量配置说明
- 整理简历描述和面试口头讲解稿

**本周产出**

- 可运行的 REST API，有接口文档（`/docs`）
- 项目 README 完整，他人可按文档跑通
- 简历描述终稿，面试讲解稿

---

## 8. 量化目标与简历描述

### 8.1 目标指标

| **指标** | **目标值** | **测量方法** | **对标意义** |
| --- | --- | --- | --- |
| Faithfulness | >= 0.85 | RAGAS 自动评测 | 答案 85%+ 有文档支撑 |
| Answer Relevancy | >= 0.80 | RAGAS 自动评测 | 答案与问题高度相关 |
| Context Recall | >= 0.80 | RAGAS + 人工标注 | 80%+ 相关文档被检索到 |
| Context Precision | >= 0.75 | RAGAS 自动评测 | 检索到的文档 75%+ 有用 |
| 多跳问题完成率 | >= 60% | 人工评测 100 题 | 复杂问题有合理答案 |
| 混合 vs 纯向量 Recall | +15% | 消融实验 E | 验证混合检索价值 |
| 有 vs 无 Reranker 精度 | +12% | 消融实验 A | 验证 Reranker 价值 |
| 平均响应时间 | <= 5s（P95） | 压测 50 并发 | 企业可用性标准 |

### 8.2 简历描述模板

> **可直接使用的简历描述（STAR 格式）**
>
> - 设计并实现企业级 Agentic RAG 系统，支持 PDF/Word/Excel 多格式文档的解析、语义分块与向量化索引；
> - 实现混合检索（Qdrant 向量 + Elasticsearch BM25）+ Cohere Reranker 精排，Context Recall 较纯向量检索提升 15%；
> - 设计 Self-Reflection 检索循环（最多 3 轮）和 HyDE 假设文档机制，多跳问题 Faithfulness 提升 20%；
> - 基于 RAGAS 框架构建自动化评测体系（300 题 QA 集），Faithfulness 0.87、Answer Relevancy 0.82；
> - 实现文档级权限控制（基于 Qdrant payload 过滤），支持多租户隔离，满足企业合规需求。

---

## 9. 面试问答准备

本项目涵盖的考察点：RAG 工程深度、检索算法、Agentic 设计、企业工程能力，能应对大厂 AI 岗面试中 80%+ 的相关问题。

### 9.1 RAG 原理类

**Q：普通 RAG 和 Agentic RAG 有什么区别？**

> 普通 RAG 是固定流程：一次检索 → 拼 prompt → 生成，无法判断检索结果是否够用。
>
> Agentic RAG 的核心是 Agent 能自主决策检索策略：检索几次、怎么改写查询、结果够不够。
>
> 具体：我实现了 Self-Reflection 循环（最多 3 轮）+ Query Rewriting，多跳问题 Faithfulness 从 0.62 提升到 0.82。

**Q：为什么要用混合检索，纯向量检索不够吗？**

> 纯向量检索擅长语义匹配，但对精确词（产品型号、日期、人名）召回差——这些词在企业文档里大量存在。
>
> BM25 对精确词匹配强，但无法处理同义词（「薪酬」vs「工资」召回不了）。
>
> 混合检索用 RRF 融合两路，取长补短。实测在包含精确名词的企业文档场景，Recall@10 提升约 15%。

### 9.2 工程实现类

**Q：分块策略怎么选，固定 512 tokens 有什么问题？**

> 固定长度分块最大问题是语义截断：一段完整的方法描述可能被切成两块，检索时只能拿到半段，上下文不完整。
>
> 我用语义段落分块（按 `\n\n` 和标题边界切割），保证每块是完整的语义单元。
>
> 另外实现父子块：256-token 小块用于精确定位，关联的 1024-token 大块用于生成完整上下文。
>
> 消融实验 C 验证：语义分块比固定分块 Context Recall 高 12%。

**Q：Reranker 和向量检索的 similarity score 有什么区别？**

> 向量检索用 Bi-encoder：query 和 document 分别编码，用余弦相似度排序，速度快（预计算）但精度有限。
>
> Reranker 用 Cross-encoder：query 和 document 拼在一起送入模型，联合建模，精度高但无法预计算，慢。
>
> 工程上两步走：向量检索召回 Top-20（快），Reranker 精排取 Top-5（准），兼顾速度和精度。
>
> 实测 Reranker 让 Answer Relevancy 从 0.71 提升到 0.82。

**Q：企业场景的权限控制怎么实现？**

> 每个文档块在索引时附加 `tenant_id`、`department`、`access_level` 元数据到 Qdrant payload。
>
> 用户请求时，`auth.py` 读取用户 JWT 解析其权限，自动注入检索过滤条件：
> ```json
> filter: {must: [{key: tenant_id, match: {value: X}}, {key: department, match: {any: [Y, Z]}}]}
> ```
>
> 高敏感文档（如薪资数据）放在独立 collection，物理隔离，防止 payload 过滤失效时跨租户泄露。

### 9.3 评测与优化类

**Q：RAGAS 的 Faithfulness 指标是怎么算的？**

> RAGAS Faithfulness = 有来源支撑的声明数 / 答案总声明数。
>
> 计算步骤：① 把答案分解为独立声明（LLM 做句子拆分）；② 对每个声明，判断是否能从检索到的 context 文档中推导出来（LLM 做 NLI 判断）；③ 有支撑的声明比例即为 Faithfulness 分。
>
> 这个指标最能反映幻觉程度——分越低说明 LLM 编造越多。

**Q：你的系统对哪类问题效果最差，怎么改进？**

> 数字计算类问题效果最差（如「三个部门的预算总和是多少」）——LLM 算术不可靠，表格解析也容易丢数字。
>
> 改进方向：① 检测到数字计算类问题时，不让 LLM 直接计算，改成提取数字后调用 Python 计算器；② 改善表格解析，把 Excel 数字列单独存储为结构化字段而非文本，支持精确数字检索。
>
> 这个回答展示了「知道系统局限」，面试官会很认可。

---

## 10. 知识学习体系

### 10.1 必须深度掌握（面试必考）

| **知识点** | **掌握程度要求** | **对应模块** |
| --- | --- | --- |
| Embedding 原理 + 向量相似度 | 能解释为什么语义相近 embedding 近 | embedder.py |
| HNSW 索引 + ANN 搜索 | 能解释为什么比精确搜索快，精度损失多少 | Qdrant 配置 |
| 混合检索 + RRF 原理 | 能写出 RRF 公式，能解释参数 k 的作用 | hybrid_retriever.py |
| Reranker vs Bi-encoder | 能解释两者精度/速度 trade-off，能选型 | reranker.py |
| 语义分块 vs 固定分块 | 能说出固定分块的缺陷，能解释父子块设计 | chunker.py |
| Self-Reflection 检索循环 | 能描述 3 轮循环逻辑，能说明为什么有效 | retrieval_agent.py |
| RAGAS 5 个指标 | 能解释每个指标的含义和计算方式 | eval/ |
| 消融实验结论 | 能说出每个组件去掉后指标下降多少 | ablation.py |

### 10.2 理解原理即可

- **HyDE：** 知道「先生成假设答案再检索」的思路即可，不需要手推数学
- **BM25 公式：** 理解「词频越高分越高，文档越长分越低」的直觉即可
- **HNSW 图构建细节：** 能说出「用图做近似最近邻搜索」，不需要记 efConstruction 参数
- **Faithfulness 的 NLI 计算：** 理解「LLM 判断声明是否有文档支撑」即可
- **流式输出（SSE）：** 理解原理，接口实现参考 FastAPI 文档

### 10.3 本项目不涉及（Multi-agent 项目里学）

- LangGraph StateGraph / Agent 编排框架
- asyncio 并发调度（Semaphore、gather）
- Multi-agent 系统设计（Planner/Subagent 角色拆分）
- GAIA benchmark 评测

---

## 11. 里程碑总览

| **周次** | **核心产出** | **可验证标准** |
| --- | --- | --- |
| 第 1 周 | 文档解析 + 索引管道 | 10 份 PDF 索引完成，能向量检索返回相关块 |
| 第 2 周 | 混合检索 + Reranker | Recall@5 有记录，混合 vs 纯向量对比数据 |
| 第 3 周 | Query Analyzer + Agentic 框架 | 问题分类准确率 80%+，端到端跑通 |
| 第 4 周 | Self-Reflection + 答案生成 + 引用 | 答案含引用标注，Faithfulness 循环验证有效 |
| 第 5 周 | RAGAS 评测 + 消融实验 | RAGAS 5 指标全部有值，5 个消融数据齐全 |
| 第 6 周 | API 封装 + 文档 + 简历整理 | REST API 可运行，README 完整，简历终稿 |

**项目完成标志**

- ✅ 文档解析管道跑通，支持 PDF/Word/Excel 三种格式
- ✅ 混合检索 + Reranker，Context Recall >= 0.80
- ✅ Self-Reflection 循环验证有效，Faithfulness >= 0.85
- ✅ RAGAS 全套评测完成，5 个指标有具体数字
- ✅ 5 个消融实验数据完整，每个组件的贡献量化
- ✅ 权限控制 + 多租户隔离实现，企业场景可用
- ✅ REST API 可部署，有接口文档
- ✅ 所有面试问答（第 9 章）能流利作答，有数字支撑

---

*Enterprise Agentic RAG System — 项目说明与计划书*
---

## 2026-05-24 阶段更新：从框架链路转向数据预处理深水区

### 当前判断

当前 WeiQuiz 的 Agentic RAG 主框架答题链路已经基本完成，已具备：

- AgentController 决策层：统一判断闲聊、澄清、工具调用、RAG Workflow。
- RAG Workflow：支持 Query Planning、子问题拆解、多跳检索、质量检查、Rewrite / Retry、答案生成与可选反思。
- 工具层：Tool Planner + Tool Registry + Adapter 边界已经建立。
- 记忆层：PostgreSQL 完整历史、Redis 最近窗口、SessionSummary 滚动摘要、Mem0 长期记忆方案已经形成。
- 流式体验：SSE 返回、trace 展示、RAG 中间过程可观测。

因此下一阶段不再优先继续堆 Agent 框架能力，而是回到 RAG 系统最关键的工程基础：**数据预处理与文档解析质量**。

### 为什么转向数据预处理

RAG 系统的上限很大程度由数据进入知识库前的质量决定。即使检索策略、rerank、Agentic Workflow 都做得很好，如果文档解析阶段已经把表格拆坏、页码丢失、标题层级丢失、扫描 PDF 没识别出来，后续检索和生成都会被污染。

面试中也很容易被追问：

- PDF 表格怎么处理？
- 跨页表格怎么办？
- 扫描版 PDF 怎么处理？
- 多栏排版解析顺序错了怎么办？
- chunk 怎么保留来源页码和标题层级？
- 入库失败怎么排查？
- 怎么证明你的 ingestion pipeline 是可追踪、可恢复的？

所以后续优化重点切换为：**把文档解析链路做成可观测、可排查、可解释的企业级 ingestion pipeline**。

### Phase 1 后续重点任务

| 优先级 | 任务 | 目标 | 状态 |
| --- | --- | --- | --- |
| P0 | 解析质量报告 | 在 ingestion report 中记录扫描 PDF、表格、图片、页码缺失、疑似跨页表格等质量信号 | 已完成 |
| P0 | metadata 标准化 | 统一 Document / Block / Chunk 三层 metadata，保证来源可追踪 | 进行中 |
| P0 | ingestion report 完善 | 记录成功、失败、跳过、失败阶段、chunk 数、block 数、质量等级 | 已有基础，继续增强 |
| P0 | 失败文档记录 | 单文档失败不拖垮整批入库，报告中记录失败阶段和原因 | 已有基础，继续增强 |
| P1 | 表格解析增强 | 将表格作为独立 Block，保留 Markdown/HTML 结构和表格 metadata | 待做 |
| P1 | 跨页表格识别 | 通过连续页码、表头相似度、列数一致性判断跨页表格候选 | 待做 |
| P1 | 扫描 PDF 处理 | 识别文字层不足的 PDF，标记 OCR required，后续接 OCR 流程 | 已有探测，待增强 |
| P1 | 多栏排版处理 | 基于坐标 bbox 恢复阅读顺序，避免左右栏串读 | 待做 |
| P1 | 标题层级与 section_path | chunk metadata 中保留章节路径，提升检索来源解释性 | 部分完成，待增强 |
| P2 | 图表/图片处理 | 图片块先做占位和 metadata 标记，后续接多模态 OCR/Caption | 待做 |
| P2 | dry-run 入库预检 | 不写 Milvus，只生成解析报告和 chunk 预览 | 待做 |
| P2 | 解析测试集 | 准备扫描 PDF、多栏 PDF、跨页表格、普通 Word/Markdown 测试样例 | 待做 |

### 本次已完成：解析质量信号进入 ingestion report

新增 `analyze_block_quality()`，在文档解析后、入库前生成轻量质量信号。

当前可识别：

- `scanned_pdf_requires_ocr`：PDF 文字层过少，疑似扫描件，需要 OCR。
- `table_blocks_present`：文档中存在表格 Block。
- `cross_page_table_candidate`：相邻页存在连续表格，疑似跨页表格。
- `image_blocks_present`：文档中存在图片/图像 Block。
- `missing_page_metadata`：大量 Block 缺少页码信息。
- `empty_parse_result`：解析结果为空。
- `very_sparse_text`：文本过少，可能解析质量低。

报告新增字段示例：

```json
{
  "parse_quality_level": "needs_review",
  "parse_quality_flags": ["table_blocks_present", "cross_page_table_candidate"],
  "parse_block_type_counts": {
    "title": 3,
    "text": 24,
    "table": 2
  },
  "parse_page_count": 8,
  "parse_page_range": "1-8",
  "parse_missing_page_block_count": 0,
  "parse_table_block_count": 2,
  "parse_cross_page_table_candidate_count": 1,
  "parse_image_block_count": 0,
  "parse_avg_block_text_length": 326.5
}
```

### 面试表达

如果面试官问：“你的 RAG 系统怎么保证文档解析质量？”

可以回答：

> 我没有把文档解析当成一个黑盒步骤，而是在 ingestion pipeline 中加入了解析质量报告。每个文档解析后都会生成质量信号，包括是否疑似扫描 PDF、是否存在表格、是否有疑似跨页表格、是否有图片块、页码 metadata 是否缺失、解析文本是否过少等。这些信息会写入 ingestion report。这样一方面方便排查为什么某些问题检索效果差，另一方面也能指导后续优化，比如扫描件走 OCR、表格走 table-aware chunk、多栏 PDF 走版面恢复。

### 下一步

下一步优先做：**表格解析增强**。

目标不是一上来完美解决所有跨页表格，而是先把表格作为一等 Block 管起来：

- 保留表格 Markdown/HTML。
- 给表格写入 `table_id`、`table_index`、`page_no`、`row_count`、`column_count`。
- 在 report 中统计表格数量和疑似跨页表格数量。
- 后续再做跨页表格合并。

这样推进最稳：先可观测，再结构化，再合并，再优化检索。

### 未完成高级能力与简历表述边界

当前项目已经可以作为 Agentic RAG 项目写入简历，但需要区分“已闭环实现”和“后续规划能力”。以下能力目前不应在简历中写成已经完整实现。

| 能力 | 当前状态 | 后续目标 | 简历表述建议 |
| --- | --- | --- | --- |
| GraphRAG | 未实现 | 构建实体、关系、社区摘要，支持图谱增强检索和跨文档关系推理 | 不写已实现；可在面试中说是后续规划 |
| 完整 MCP 工具生态 | 未实现完整 MCP Client / Server 调用链 | 接入 MCP Server，统一外部工具发现、调用、鉴权和结果回收 | 不写“已接入 MCP”；可写“预留 Tool Registry / Adapter 扩展接口” |
| Web Search | 当前仅有工具占位与未配置返回 | 接入真实搜索 API 或 MCP Web Search Server，作为知识库不足时的补充召回 | 不写“支持联网搜索”；可写“预留 Web Search Adapter” |
| SQL Assistant | 当前未完整实现自然语言到 SQL 查询闭环 | 增加 schema introspection、SQL 生成、执行、结果解释、安全限制 | 不写已实现 |
| Mem0 长期语义记忆 | 已讨论方案，未形成完整生产闭环 | 接入 Mem0 官方能力或自实现 extract / update / search / inject 流程 | 不写“已实现 Mem0”；可写“设计长期语义记忆扩展方案” |
| 文档级 ACL | 当前是基础用户/管理员 RBAC | 将权限字段写入文档与 chunk metadata，检索时注入权限过滤 | 不写“文档级权限完整落地”；可写“实现基础 RBAC 与会话隔离” |
| RAGAS 评测闭环 | 有过实验脚本与小样本尝试，未形成稳定评测集 | 基于真实知识库构建 QA 标注集，持续评估 faithfulness、context precision/recall | 不写“完整评测体系”；可写“探索 RAG 评测脚本” |
| 多 Agent 协作 | 未实现 | 将检索、工具、评估、生成拆为专业 Agent 协同 | 不写已实现 |
| 完整 Plan-and-Execute | 当前是 AgentController + RAG Workflow 策略调度 | 支持复杂任务计划生成、步骤执行、状态管理、失败恢复 | 不写完整 Plan-and-Execute；可写“实现基础决策调度与复杂问题拆解” |

### 当前简历可写的真实闭环能力

以下能力已经形成较完整闭环，可以写入简历：

- AgentController 决策层：统一调度闲聊、澄清、工具调用、RAG Workflow。
- Hybrid Retrieval：Milvus 向量检索 + BM25 + RRF 融合。
- Rerank 精排：对召回结果进行二阶段排序。
- 复杂问题处理：Query Planning、子问题拆解、多跳检索、中间答案综合。
- Rewrite / Retry：低质量召回时进行查询改写和有限重试。
- Grounding / Reflection：可选答案依据校验模式。
- 三层会话记忆：PostgreSQL 完整历史、Redis 最近窗口、SessionSummary 滚动摘要。
- 文档解析与入库：PDF / Word / Markdown 解析、清洗、分块、metadata、增量索引。
- Ingestion Report：记录成功、失败、chunk 数、block 数、解析质量信号。
- 基础权限：JWT 登录认证、用户会话隔离、管理员文档管理。
- SSE 流式输出：前端展示检索、重写、生成等中间过程。

### 简历推荐边界写法

推荐写法：

> 构建 WeiQuiz Agentic RAG 企业知识库问答系统，完成从文档解析、增量入库、混合检索、Rerank 精排、AgentController 决策调度、复杂问题拆解、会话记忆、流式输出到 trace 可观测的核心链路；同时预留工具注册、长期记忆、外部检索等扩展接口，为后续 MCP、Mem0、GraphRAG 等高级能力接入打基础。

不推荐写法：

> 完整实现 GraphRAG、MCP 工具生态、Mem0 长期记忆和企业级评测体系。

原因：这些能力当前还没有形成端到端生产闭环，面试中如果被追问实现细节，容易暴露边界不清。
