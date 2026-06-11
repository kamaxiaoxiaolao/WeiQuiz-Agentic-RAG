# 架构说明

WeiQuiz 的架构目标很明确：让 RAG 问答链路可解释、可排查、可扩展。

它不是把“检索 + 生成”写成一个黑盒函数，而是把用户请求拆成路由、记忆、检索、质量检查、答案生成、引用溯源和 Trace 等多个可观察阶段。

## 请求链路

```text
用户消息
  -> FastAPI /chat/stream
  -> JWT 鉴权与会话归属检查
  -> MemoryService 构建最近上下文和会话摘要
  -> LongTermMemoryService 可选检索长期记忆
  -> AgentController 判断执行模式
  -> RAG Workflow 或 Tool Call Workflow
  -> SSE 返回 route、step、answer chunk、citation、trace
  -> 持久化对话内容和元数据
```

## AgentController

AgentController 是系统的决策层。它不直接检索文档，也不直接生成最终答案，而是返回一个结构化决策：

- `mode`：闲聊、RAG Workflow、工具调用或澄清。
- `route`：意图、路由方式、置信度、策略和原因。
- `memory_policy`：本轮回答应该使用哪些记忆层。
- `tool_plan`：可选的工具调用计划。
- `rag_strategy`：direct、decomposition、HyDE、step-back、web search、SQL 或 chitchat。
- `need_grounding`：是否需要执行答案依据校验。
- `max_retries`：rewrite / retrieval 的重试预算。

这种拆分让路由决策可以独立测试，也避免 API 层堆积大量条件判断。

## RAG Workflow

```text
Route
  -> 可选 Query Transform
      -> Decomposition：适合宽泛、对比、归纳类问题
      -> HyDE：适合语义表达较弱、直接检索不稳定的问题
      -> Step-back：适合需要先抽象背景的问题
  -> Retrieval
  -> Quality Check
  -> 可选 Rewrite / Retry
  -> 多步路径的 Intermediate Synthesis
  -> Final Answer Generation
  -> Optional Grounding
```

Workflow 会向 API 层发出步骤事件，前端可以展示每一步发生了什么，方便演示和排查。

## 检索链路

```text
Query
  -> Dense Vector Retriever
  -> Stateful BM25 Retriever
  -> RRF Fusion
  -> Parent Context / Auto-merging / Table Context
  -> Rerank
  -> SourceNodePayload
```

Dense 检索提升语义召回能力；BM25 提升精确词、编号、配置项、接口名、实体名等召回能力；RRF 避免直接比较不同检索器的分数；Rerank 作为二阶段精排，提高最终上下文相关性。

## 层级 Chunk

WeiQuiz 使用层级节点：

- Root：更大的文档级上下文。
- Parent：适合生成阶段使用的上下文。
- Leaf：适合检索阶段使用的小块。

Leaf 节点用于提升检索精度，Parent / Root 节点用于保留生成所需的完整上下文。当同一个 Parent 下足够多的兄弟 Leaf 被命中时，Auto-merging 可以用 Parent 级上下文替代多个零散 Leaf。

## 文档入库链路

```text
扫描 docs_dir
  -> SHA256 diff
  -> 解析文件为 blocks
  -> 清洗和标准化 blocks
  -> 必要时合并或切分表格
  -> 构建 section documents
  -> 构建 hierarchical nodes
  -> leaf nodes 写入向量后端
  -> parent/root context 写入 PostgreSQL
  -> 更新 BM25 状态
  -> 写入 ingestion report
```

Ingestion report 对排查 RAG 效果非常重要。很多回答不准的问题，根因并不在生成模型，而在文档解析、清洗、分块或 metadata 阶段。

## 记忆层

| 层级 | 存储 | 作用 |
| --- | --- | --- |
| Recent Memory | Redis 或进程内兜底 | 最近对话上下文 |
| Full History | PostgreSQL | 完整历史和审计回放 |
| Session Summary | PostgreSQL | 长会话滚动压缩 |
| Long-term Memory | 可选 Mem0 | 跨会话偏好、目标和稳定事实 |

最终 Prompt 接收的是压缩后的 memory context，而不是完整消息历史。这样可以兼顾多轮上下文和 token 成本。

## API 分组

主要接口分组：

- `/auth/*`：注册、登录、当前用户信息。
- `/chat/stream`：SSE 流式聊天主入口。
- `/sessions/*`：用户会话生命周期。
- `/documents/*`：上传、重建索引、删除和知识库列表。
- `/admin/*`：用户、审计日志和管理概览。
- `/debug/*`：记忆调试和手动压缩。

## 运行时服务

本地开发主要使用：

- FastAPI：后端 API。
- Vue / Vite：前端应用。
- PostgreSQL：用户、会话、摘要、审计日志和 parent chunk context。
- Redis：最近记忆、临时任务状态和轻量缓存。
- Chroma：默认本地向量库。
- Milvus：可选的生产化向量服务。
- Phoenix：可选 Trace 平台。

## 常见故障定位

- 答案错但来源正确：优先检查生成 Prompt 或 Grounding 策略。
- 答案错且来源也错：检查检索、Rerank、BM25 状态和 Query Strategy。
- 没有答案：检查向量索引、Embedding 配置、文档入库状态和 `TOP_K`。
- 上下文缺失：检查 chunk size、parent store 和 auto-merging。
- 用户数据串扰：检查鉴权依赖和 session ownership 校验。
