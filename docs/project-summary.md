# WeiQuiz Agentic RAG 项目总结

## 1. 项目定位

WeiQuiz 是一个面向企业知识库场景的 Agentic RAG 问答系统。

项目核心目标不是做一个简单的“文档向量搜索 Demo”，而是围绕企业知识问答中常见的问题，构建一套从文档入库、混合检索、复杂问题处理、答案生成、会话记忆、权限控制到过程可观测的完整 RAG 链路。

一句话描述：

> WeiQuiz 是一个基于 FastAPI、LlamaIndex、pgvector/Milvus、Redis、PostgreSQL 构建的 Agentic RAG 企业知识库系统，支持文档解析入库、Hybrid Retrieval、Rerank、复杂问题拆解、会话记忆、流式输出和 RAG 过程追踪。

## 2. 技术栈

| 模块 | 技术选型 | 作用 |
| --- | --- | --- |
| 后端框架 | FastAPI | 提供认证、聊天、文档管理、流式问答 API |
| RAG 框架 | LlamaIndex | 文档节点、检索、Query Engine、RAG 组件编排 |
| 向量数据库 | pgvector 默认，Milvus 可选 | 存储向量节点并执行向量检索 |
| 关键词检索 | BM25 | 补充精确词、编号、状态码、专有名词召回 |
| 融合排序 | RRF | 融合向量检索与 BM25 检索结果 |
| 精排模型 | DashScope Rerank | 对候选文档进行二阶段重排序 |
| 大模型 | DashScope OpenAI-Compatible API | Query Planning、答案生成、反思校验等 |
| 数据库 | PostgreSQL | 保存用户、会话、消息、父级 chunk、摘要等结构化数据 |
| 缓存 | Redis | 缓存最近会话窗口和热点上下文 |
| 鉴权 | JWT | 用户登录认证和接口权限控制 |
| 前端 | Vue 3 Demo / Streamlit | 展示聊天、文档管理、RAG 中间过程 |
| 流式输出 | SSE | 实现逐 token 返回和中间步骤展示 |
| 文档解析 | unstructured、pypdf、docx/xml、HTML parser | 解析 PDF、Word、Markdown、HTML 等文档 |

## 3. 系统整体架构

当前系统可以拆成七层：

```text
用户界面层
  -> FastAPI 接口层
  -> AgentController 决策层
  -> RAG Workflow 执行层
  -> 检索与重排层
  -> 文档解析与索引层
  -> 存储与缓存层
```

各层职责如下：

| 层级 | 职责 |
| --- | --- |
| 用户界面层 | 提供聊天、文档上传、历史会话和 RAG 过程展示 |
| 接口层 | 处理认证、请求校验、SSE 流式响应、错误返回 |
| AgentController 决策层 | 判断请求走闲聊、澄清、工具调用还是 RAG Workflow |
| RAG Workflow 执行层 | Query Planning、子问题拆解、多跳检索、质量检查、重写、生成 |
| 检索与重排层 | pgvector/Milvus 向量检索、BM25、RRF 融合、Rerank 精排 |
| 文档解析与索引层 | 文档扫描、解析、清洗、切分、metadata、增量入库 |
| 存储与缓存层 | PostgreSQL/pgvector、Redis、可选 Milvus 分别保存结构化数据、缓存和向量索引 |

## 4. 已实现核心功能

### 4.1 文档解析与入库

项目支持将本地知识库文档解析为结构化 Block，再转换为 section 级 Document，最后进行层级切分并写入向量库与 PostgreSQL。

已实现能力：

- 支持 PDF、Word、Markdown、HTML、Text 等文档格式。
- 支持 SHA256 增量扫描，识别新增、更新、删除文档。
- 支持 PDF 文本层探测，识别疑似扫描 PDF。
- 支持 `title / text / list_item / table / image / unknown` Block 抽象。
- 支持页眉页脚、页码、目录、公式碎片、HTML 导航噪声等清洗规则。
- 支持 section 级合并，避免每个小 block 直接入库导致上下文过碎。
- 支持 hierarchical chunk，形成 root / parent / leaf 层级节点。
- 支持 leaf 节点用于检索，parent/root 节点用于上下文扩展。
- 支持 audit markdown，将解析后的文档和 section 输出为可检查文件。

### 4.2 Ingestion Report

项目实现了入库报告，用于记录每次文档同步的结果。

报告内容包括：

- 新增、更新、删除文档数量。
- 成功文档数和失败文档数。
- 失败阶段和失败原因。
- 文档 block 数、chunk 数。
- section 长度统计。
- chunk 长度统计。
- root / parent / leaf 节点统计。
- 文件类型分布。
- 文档解析质量信号。

解析质量信号包括：

- 是否疑似扫描 PDF。
- 是否需要 OCR。
- 是否存在表格 block。
- 是否存在疑似跨页表格。
- 是否存在图片 block。
- 是否缺少页码 metadata。
- 是否解析结果为空。
- 是否文本过少。

这部分主要解决企业 RAG 中的可排查问题：当答案不准时，可以判断问题来自文档解析、清洗、切分、检索还是生成。

### 4.3 Metadata 标准化

项目中抽象了稳定的 metadata schema，避免不同模块传递松散字典。

当前主要 metadata 层级：

- Document metadata：文档级来源信息。
- Section metadata：预处理后的章节级信息。
- Hierarchy node metadata：root / parent / leaf 节点信息。
- Canonical chunk metadata：检索与展示统一使用的 chunk 信息。
- SourceNodePayload：API 返回给前端的来源结构。

关键字段包括：

- `doc_id`
- `source_path`
- `file_name`
- `file_type`
- `section_id`
- `section_title`
- `section_path`
- `page_range`
- `chunk_id`
- `parent_id`
- `chunk_role`
- `chunk_strategy`

这些字段保证系统能够回答：

- 这个答案来自哪个文档？
- 来自哪一页或哪个页码范围？
- 来自哪个章节？
- 当前 chunk 是 leaf 还是 parent？
- 是否由 Auto-merging 扩展得到？

### 4.4 混合检索

项目使用 Hybrid Retrieval，而不是只依赖向量检索。

原因是企业文档中经常包含：

- 状态码，如 `401`、`403`。
- 产品型号。
- 接口名称。
- 人名、部门名。
- 精确配置项。

向量检索擅长语义相似，但对精确词匹配不稳定；BM25 擅长关键词匹配，但不理解语义相似。因此项目采用：

```text
Dense Vector Retrieval + BM25 Sparse Retrieval -> RRF Fusion -> Rerank
```

当前检索链路：

1. 对用户问题生成向量。
2. 使用 pgvector/Milvus 做向量召回。
3. 使用 BM25 做关键词召回。
4. 使用 RRF 融合两路结果。
5. 使用 Rerank 对候选结果精排。
6. 返回 top context 给生成模型。

### 4.5 Rerank 精排

Rerank 的作用是解决第一阶段召回“召回多但排序不够准”的问题。

当前设计：

- 第一阶段检索取较大的候选集。
- Rerank 模型逐个判断 query 与 context 的相关性。
- 取精排后的 Top-K 文档进入答案生成。

面试表达：

> 向量检索和 BM25 更适合作为召回层，目标是尽量不要漏掉相关内容；Rerank 作为精排层，更关注 query-context 的细粒度相关性，因此放在召回之后、生成之前。

### 4.6 AgentController 决策层

项目引入 AgentController 作为 Agentic RAG 的决策层。

它不直接检索、不执行工具、不生成最终答案，而是负责判断请求的执行路径。

当前支持四类模式：

- `chitchat`：闲聊，直接回答。
- `clarification`：问题信息不足，先反问用户。
- `tool_call`：需要调用工具。
- `rag_workflow`：进入知识库 RAG Workflow。

AgentController 输出结构化 `AgentDecision`，包括：

- 执行模式。
- 路由结果。
- 澄清问题。
- 工具调用计划。
- RAG 策略。
- 记忆策略。
- 是否需要 Grounding。
- 最大重试次数。

这样做的好处是把“决策”和“执行”解耦，避免所有逻辑堆在接口层。

### 4.7 RAG Workflow

RAG Workflow 是实际执行 Agentic RAG 的主链路。

当前支持：

- Query Planning。
- 复杂问题识别。
- 子问题拆解。
- 多跳检索。
- 检索质量检查。
- Query Rewrite。
- Retry，默认最大重试次数为 1。
- Intermediate Synthesis。
- 最终答案生成。
- 可选 Grounding / Reflection。

典型复杂问题链路：

```text
用户问题
  -> AgentController 判断进入 RAG Workflow
  -> Query Planning
  -> 子问题拆解
  -> 分别检索每个子问题
  -> 生成子答案和证据
  -> 综合中间结果
  -> 生成最终答案
  -> 可选 Grounding 校验
```

### 4.8 澄清机制

项目实现了前置澄清机制。

触发场景：

- “对比这两个”
- “哪个更好”
- “总结这个”
- “分析这个问题”
- 缺少明确对象、范围、条件的问题

设计原因：

如果问题本身信息不足，直接检索会召回噪声，甚至导致模型编造。前置澄清可以在进入 RAG 前降低错误检索和幻觉风险。

当前实现：

- 规则优先，处理高确定性模糊表达。
- LLM fallback，处理边界模糊问题。
- 输出澄清问题给用户。

### 4.9 Rewrite / Retry

当初次检索质量不足时，系统可以触发 Query Rewrite。

设计原则：

- 不无限重试。
- 默认最大重试次数为 1。
- 避免 Agentic RAG 因多轮 LLM 调用导致延迟和 token 成本过高。

面试表达：

> 我们不是每个问题都重写，而是在质量检查不足时才触发，并设置最大重试次数，保证效果和成本之间的平衡。

### 4.10 Grounding / Reflection

项目支持可选答案反思校验。

模式包括：

- `off`：关闭。
- `auto`：由 AgentController 判断是否需要。
- `reflection`：强制开启。

作用：

- 检查答案是否有检索上下文支撑。
- 降低幻觉。
- 在复杂问题或高风险问题中提升可信度。

由于 Reflection 会额外调用 LLM，因此默认不对所有问题强制开启。

### 4.11 会话记忆系统

项目实现三层会话记忆：

| 层级 | 作用 |
| --- | --- |
| PostgreSQL | 保存完整会话历史 |
| Redis / ChatMemoryBuffer | 保存最近窗口，提高读取速度 |
| SessionSummary | 对旧消息做滚动摘要压缩 |

当前记忆链路：

1. 用户每轮问答写入 PostgreSQL。
2. 最近消息同步到 Redis。
3. 超过阈值后触发摘要压缩。
4. 后续回答时注入“摘要 + 最近窗口”。

这种设计解决了上下文窗口有限的问题：

- 最近消息保留原文，保证短期对话连续性。
- 旧消息压缩成摘要，降低 token 成本。
- PostgreSQL 保存完整历史，保证可追溯。

### 4.12 流式输出与过程可观测

项目使用 SSE 实现流式回答。

前端可以展示：

- 路由结果。
- Controller 决策。
- Query Planning。
- 子问题拆解。
- 多跳检索。
- 质量检查。
- Query Rewrite。
- 中间答案综合。
- 最终答案生成。
- trace 信息。

这部分使系统不再是黑盒问答，而是能展示 RAG 中间过程。

### 4.13 账号与权限系统

项目实现基础账号与权限体系。

已实现：

- 用户注册。
- 用户登录。
- JWT Bearer Token 鉴权。
- `/auth/me` 获取当前用户信息。
- 用户会话隔离。
- 管理员文档管理权限。

当前权限边界：

- 普通用户：聊天、查询自己的会话、删除自己的会话。
- 管理员：文档上传、删除、文档列表查询。

后续可扩展：

- 文档级 ACL。
- chunk 级权限过滤。
- 审计日志。
- 多租户隔离。

## 5. 当前未完成但已规划的能力

以下能力当前不应在简历中写成“完整实现”，只能写成规划或预留扩展。

| 能力 | 当前状态 | 后续方向 |
| --- | --- | --- |
| GraphRAG | 未实现 | 构建实体关系图谱、社区摘要、图谱增强检索 |
| 完整 MCP 工具生态 | 未实现完整闭环 | 接入 MCP Server，实现工具发现、调用、鉴权和结果回收 |
| Web Search | 只有工具占位 | 接入真实搜索 API 或 MCP Web Search |
| SQL Assistant | 未完整实现 | NL2SQL、schema introspection、安全 SQL 执行 |
| Mem0 长期语义记忆 | 有方案设计，未形成完整生产闭环 | 接入官方 Mem0 或自实现 extract / update / search / inject |
| 文档级 ACL | 只有基础 RBAC | chunk metadata 注入权限字段，检索时自动过滤 |
| RAGAS 评测闭环 | 有实验尝试，未稳定沉淀 | 构建真实 QA 标注集，持续评估 RAG 效果 |
| 完整 Plan-and-Execute | 当前是基础决策调度 | 引入更完整的计划生成、步骤执行、状态管理和失败恢复 |

## 6. 项目亮点

### 6.1 不只是普通 RAG

普通 RAG 通常是：

```text
用户问题 -> 单次检索 -> 拼上下文 -> 生成答案
```

WeiQuiz 的链路是：

```text
用户问题
  -> AgentController 决策
  -> 澄清 / 闲聊 / 工具 / RAG Workflow 分支
  -> Hybrid Retrieval + Rerank
  -> 复杂问题拆解与多跳检索
  -> 质量检查与有限重试
  -> 生成答案
  -> 可选 Grounding
  -> SSE 展示过程
```

### 6.2 重视数据预处理

项目没有只关注检索和 Agent，而是补充了文档解析、清洗、metadata、ingestion report、解析质量信号等基础设施。

这是 RAG 项目能否稳定运行的关键。

### 6.3 可观测性较强

系统能记录：

- 文档入库过程。
- 文档解析质量。
- 检索结果。
- Rerank 分数。
- Controller 决策。
- RAG trace。
- 会话记忆状态。

当答案不准时，可以按链路排查。

## 7. 面试讲解版本

如果面试官让你介绍项目，可以按下面结构讲：

> 我做的是一个面向企业知识库的 Agentic RAG 系统。它不是简单的向量搜索 Demo，而是从文档解析、增量入库、metadata 标准化、混合检索、Rerank、复杂问题拆解、会话记忆到流式可观测的一整套链路。
>
> 数据进入系统时，会先做文档扫描和 hash diff，识别新增、更新、删除文件。解析阶段会把 PDF、Word、Markdown 等文档转换成 Block，并做清洗、section 合并和层级 chunk。每个 chunk 都保留 doc_id、source_path、page_range、section_title、chunk_role 等 metadata，保证答案可追溯。
>
> 检索阶段使用向量检索和 BM25 的混合检索，再用 RRF 做融合，最后接 Rerank 精排。这样既能处理语义问题，也能处理企业文档里常见的状态码、接口名、产品编号等精确词。
>
> 在 Agentic 部分，我加了 AgentController 作为决策层，判断问题应该走闲聊、澄清、工具调用还是 RAG Workflow。复杂问题会进入 Query Planning、子问题拆解、多跳检索、质量检查、Rewrite 和中间答案综合。为了控制成本，重试次数默认限制为 1。
>
> 系统还实现了三层会话记忆：PostgreSQL 保存完整历史，Redis 保存最近窗口，SessionSummary 压缩旧上下文。前端通过 SSE 展示检索、重写、生成等中间过程，方便演示和排查。

## 8. 当前最值得继续优化的方向

按照面试和项目价值排序，后续优先级是：

1. 文档解析深水区：表格、跨页表格、OCR、多栏排版。
2. 检索评估：Recall@K、MRR、Context Precision/Recall。
3. 文档级权限：chunk metadata 权限过滤。
4. Web Search / MCP：知识库不足时的外部工具补充。
5. Mem0 长期语义记忆：跨会话用户偏好与事实记忆。
6. GraphRAG：实体关系和跨文档推理增强。
