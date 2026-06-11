# WeiQuiz

面向企业知识库问答场景的 Agentic RAG 系统。

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3.x-42b883?logo=vue.js&logoColor=white)](https://vuejs.org/)
[![LlamaIndex](https://img.shields.io/badge/LlamaIndex-RAG-6f42c1)](https://www.llamaindex.ai/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

WeiQuiz 是一个端到端的企业级 Agentic RAG 项目，覆盖文档解析、增量入库、混合检索、Query Planning、多轮记忆、SSE 流式回答、引用溯源、可观测 Trace、JWT 鉴权、RBAC 权限和知识库管理。

它不是简单的“向量库 + Prompt”Demo，而是围绕真实 RAG 系统里的工程问题设计：文档解析质量不可控、Chunk 上下文不足、召回不稳定、复杂问题需要多步检索、答案容易幻觉、多轮对话丢上下文、排障困难、用户数据需要隔离。

## 项目定位

WeiQuiz 适合用于：

- 学习企业级 RAG 系统的完整链路。
- 展示 Agentic RAG、混合检索、多层记忆和可观测工程能力。
- 作为私有知识库问答、文档助手、企业制度问答、报告分析助手的基础框架。
- 面试或作品集展示，体现从 Demo 到工程化系统的设计能力。

一句话介绍：

> WeiQuiz 是一个企业级 Agentic RAG 知识库问答系统。它不仅实现了文档向量检索和 LLM 回答，还加入了 Query Router、AgentController、混合检索、层级 Chunk、Rerank、多轮记忆、SSE Trace、权限隔离和入库质量报告，重点解决真实 RAG 系统中召回不准、上下文不足、复杂问题拆解、答案幻觉和排障困难的问题。

## 项目亮点

### 1. Agentic RAG，而不是固定链路 RAG

普通 RAG 往往是固定流程：

```text
用户问题 -> 向量检索 -> 拼 Prompt -> LLM 回答
```

WeiQuiz 在 RAG 前增加了 AgentController 和 Query Router：

- 判断问题是闲聊、知识库问答、复杂多步问题、工具调用，还是需要先澄清。
- 根据问题复杂度选择 direct、decomposition、HyDE、step-back、web search、SQL 等策略。
- 检索质量不足时，可以触发 query rewrite 和 retry。
- 对复杂答案可选 Grounding 检查，判断关键结论是否被证据支撑。

这让系统从“被动检索”升级为“先决策、再执行、可回溯”的 Agentic RAG。

### 2. 混合检索：Dense + BM25 + RRF + Rerank

项目没有只依赖向量检索，而是组合了多阶段检索链路：

```text
Dense Vector Retrieval
  + Stateful BM25 Retrieval
  -> RRF Fusion
  -> Parent Context / Auto-merging / Table Context
  -> DashScope Rerank
  -> Source Nodes
```

设计原因：

- Dense Vector 适合语义召回，例如“登录凭证失效”召回“Token 过期”。
- BM25 适合精确匹配，例如接口名、配置项、版本号、政策编号、财务科目。
- RRF 基于排名融合，不强行比较向量分数和 BM25 分数。
- Rerank 做二阶段精排，提升 Top Context 与问题的相关性。

这套链路更接近生产 RAG，而不是只做一次 Top-K 向量搜索。

### 3. 层级 Chunk：小块检索，大块生成

WeiQuiz 使用 root / parent / leaf 三层节点：

```text
root chunk
  -> parent chunk
    -> leaf chunk
```

核心思想：

- leaf chunk 更短，用于检索，命中更精准。
- parent/root chunk 保留更完整上下文，用于生成。
- 向量库主要存 leaf，降低向量冗余。
- PostgreSQL 存 parent/root/leaf 元数据，支持命中 leaf 后回取 parent context。
- 当同一个 parent 下多个兄弟 leaf 被命中时，Auto-merging 可以自动合并为更完整的父级上下文。

这解决了 RAG 常见问题：块太小会丢上下文，块太大又会召回不准。

### 4. 面向真实文档的入库链路

支持 PDF、DOCX、Markdown、HTML、TXT 等格式，入库流程包括：

```text
扫描 docs_dir
  -> SHA256 diff
  -> 文档解析为 blocks
  -> 清洗与结构化
  -> 表格合并/切分
  -> section 文档
  -> hierarchical chunk
  -> leaf 写入向量库
  -> parent/root 写入 PostgreSQL
  -> 更新 BM25 状态
  -> 生成 ingestion report
```

亮点：

- SHA256 增量索引，识别新增、更新、删除。
- 解析质量报告，方便定位“答案不准到底是不是入库问题”。
- 保留 audit markdown，支持人工检查解析后的中间结果。
- 针对表格场景做了 table context postprocessor，适合财报、制度、报告类文档。

### 5. 多层记忆系统

项目将会话记忆拆成多层：

| 层级 | 存储 | 作用 |
| --- | --- | --- |
| Recent Memory | Redis / 进程兜底 | 最近几轮对话，低延迟注入 Prompt |
| Full History | PostgreSQL | 完整历史，支持审计、回放和会话列表 |
| Session Summary | PostgreSQL | 长会话滚动摘要，控制上下文长度 |
| Long-term Memory | 可选 Mem0 | 跨会话长期偏好、目标、稳定事实 |

回答时不会把所有历史直接塞进 Prompt，而是组合最近消息、滚动摘要和长期记忆，兼顾上下文连续性和 token 成本。

### 6. 全链路 SSE 和 Trace，可演示、可排查

`/chat/stream` 使用 SSE 返回结构化事件：

- `route`：本轮问题被判定为什么模式。
- `step`：Workflow 当前执行到哪一步。
- `trace`：检索、改写、质量检查、耗时等过程信息。
- `chunk`：LLM 生成的流式 token。
- `result`：最终答案、来源、引用和 trace。

前端提供聊天面板、知识库面板和 Debug Panel，方便演示“系统为什么这么回答”，也方便排查 RAG 效果。

### 7. 统一 LLM Gateway，便于模型切换和任务隔离

项目把路由、改写、HyDE、Step-back、分解、生成、Grounding、记忆摘要等 LLM 调用统一收敛到 LLM Gateway：

- 不同任务可以配置不同模型。
- 每类任务可以设置独立 timeout。
- 统一记录调用耗时、模型、输入字符量和失败信息。
- 保持 OpenAI-compatible API 形态，方便切换 DashScope、OpenAI-compatible 服务或私有模型网关。

这避免了大模型调用散落在各个模块里，后续做成本控制、超时降级和模型路由会更容易。

### 8. 工具调用链路工程化

项目设计了 `Router -> Controller -> Tool Planner -> Tool Registry -> Tool Handler` 工具链路：

- Router 判断是否需要工具。
- Controller 切换到 `TOOL_CALL` 模式。
- Tool Planner 规划工具和参数。
- Tool Registry 做权限检查、参数校验、默认值填充、同步/异步执行和结果标准化。
- API 将工具结果写入 trace 和会话历史。

这部分重点不是简单写一个函数调用入口，而是把工具调用需要的规划、校验、执行、结果标准化和 Trace 记录拆开，后续接入真实业务工具时不需要改动主聊天链路。

### 9. 具备基础生产化能力

项目包含：

- JWT 登录认证。
- admin / user 角色。
- 用户会话隔离。
- 知识库文档管理。
- 管理员用户管理。
- 审计日志。
- Docker Compose 启动 Redis / PostgreSQL / 可选 Milvus。
- 可选 Phoenix tracing。

这让项目更像完整应用，而不是 notebook 或命令行 Demo。

### 10. 评测脚本与质量闭环

项目提供了检索评估、RAGAS 评测、中文金融文档评测和 retrieval ablation 相关脚本，用于分析不同检索策略、重排策略和上下文扩展策略对回答质量的影响。

这些脚本为后续构建自动化评测报告、检索消融实验和质量回归检查提供基础。

## 整体架构

### 分层架构图

```mermaid
flowchart TB
  User["用户 / 管理员"] --> Frontend["Vue 3 前端<br/>聊天 / 会话 / 知识库 / 调试面板"]
  Frontend --> API["FastAPI 后端<br/>Auth / Session / Chat / Documents / Admin"]

  API --> Auth["认证与权限层<br/>JWT / RBAC / Session Ownership"]
  API --> Memory["记忆服务<br/>Recent Memory / Summary / Long-term Memory"]
  API --> Controller["AgentController<br/>模式决策 / MemoryPolicy / ToolPlan / RAG Strategy"]

  Controller --> Router["Query Router<br/>意图识别 / 复杂度判断 / 策略选择"]
  Controller --> ToolFlow["工具调用链路<br/>Tool Planner / Tool Registry / MCP / Web Search"]
  Controller --> Workflow["Agentic RAG Workflow<br/>Decomposition / HyDE / Step-back / Rewrite / Retry"]

  Workflow --> Retrieval["混合检索层<br/>Dense + BM25 + RRF + Rerank"]
  Retrieval --> Context["上下文增强<br/>Parent Context / Auto-merging / Table Context"]
  Context --> Generation["答案生成<br/>LLM Gateway / Citation / Optional Grounding"]
  Generation --> Stream["SSE Streaming<br/>route / step / trace / chunk / result"]
  Stream --> Frontend

  API --> Observability["可观测层<br/>RAG Trace / Ingestion Report / Phoenix"]
  API --> Ingestion["文档入库链路<br/>Parser / Cleaner / Chunker / Indexer"]

  Ingestion --> VectorStore["向量库<br/>Chroma / Milvus / pgvector"]
  Ingestion --> Postgres["PostgreSQL<br/>用户 / 会话 / 消息 / 摘要 / 父子块 / 审计"]
  Retrieval --> VectorStore
  Retrieval --> Postgres
  Memory --> Redis["Redis<br/>最近记忆 / 任务状态 / 缓存"]
  Memory --> Postgres
  Auth --> Postgres
```

### 问答时序

```mermaid
sequenceDiagram
  participant U as 用户
  participant F as Vue 前端
  participant A as FastAPI
  participant C as AgentController
  participant M as MemoryService
  participant W as RAG Workflow
  participant R as Hybrid Retrieval
  participant L as LLM Gateway
  participant DB as PostgreSQL/Redis

  U->>F: 输入问题
  F->>A: POST /chat/stream
  A->>DB: 校验用户与会话归属
  A->>M: 构建最近记忆、摘要、长期记忆
  A->>C: 判断执行模式和策略
  C-->>A: AgentDecision
  A-->>F: SSE route event
  A->>W: 启动 Agentic RAG Workflow
  W->>R: 执行 Dense + BM25 + RRF + Rerank
  R-->>W: Source Nodes
  W-->>A: Trace / Quality / Retrieval Result
  A->>L: 基于上下文流式生成答案
  L-->>A: token chunks
  A-->>F: SSE chunk / result / trace
  A->>DB: 保存消息、来源、引用和 Trace
```

### 文档入库时序

```mermaid
flowchart LR
  Upload["上传文档 / 扫描 docs_dir"] --> Diff["SHA256 Diff<br/>新增 / 更新 / 删除"]
  Diff --> Parse["解析文件<br/>PDF / DOCX / MD / HTML / TXT"]
  Parse --> Clean["清洗 blocks<br/>标题 / 正文 / 列表 / 表格"]
  Clean --> Table["表格处理<br/>跨页合并 / 大表切分"]
  Table --> Section["构建 Section Documents"]
  Section --> Chunk["Hierarchical Chunk<br/>root / parent / leaf"]
  Chunk --> Embed["Leaf Embedding"]
  Embed --> Vector["写入向量库"]
  Chunk --> ParentStore["写入 PostgreSQL Parent Store"]
  Chunk --> BM25["更新 BM25 State"]
  Diff --> Report["生成 Ingestion Report / Audit Markdown"]
```

### 数据存储关系

```text
PostgreSQL
  users                         用户表
  chat_sessions                 会话归属
  chat_messages                 完整消息历史
  session_summaries             长会话滚动摘要
  knowledge_documents           知识库文档元数据
  knowledge_ingest_jobs         入库任务记录
  audit_logs                    管理操作审计
  parent/root/leaf chunk store  父子块上下文

Redis
  recent chat memory            最近对话窗口
  ingestion job cache           入库任务临时状态
  lightweight cache             轻量缓存

Vector Store
  Chroma                        默认本地开发
  Milvus                        生产化向量服务可选
  pgvector                      PostgreSQL 向量后端可选

Local data/
  docs                          本地知识库文档
  index                         索引、BM25 状态、向量数据
  audit                         解析报告和审计 Markdown
```

## 核心功能

- Agentic RAG Workflow：路由、策略选择、HyDE、Step-back、子问题拆解、Rewrite/Retry。
- Hybrid Retrieval：Dense + BM25 + RRF + Rerank。
- Hierarchical Chunk：root / parent / leaf 多粒度上下文。
- Auto-merging：命中多个兄弟 leaf 时自动回取父级上下文。
- Table Context：增强表格类文档问答效果。
- Incremental Ingestion：基于 SHA256 的增量入库。
- Memory System：Redis 最近窗口、PostgreSQL 完整历史、SessionSummary、可选 Mem0。
- LLM Gateway：统一模型调用、任务级模型配置、timeout 和调用日志。
- Tool System：工具注册、参数校验、异步执行、MCP/Web Search 扩展点。
- SSE Streaming：步骤、Trace、答案和引用流式推送。
- Observability：RAG Trace、Source Node、Ingestion Report、可选 Phoenix。
- Auth / RBAC：JWT、用户隔离、管理员接口。
- Frontend：Vue 3 聊天界面、会话列表、知识库管理、调试面板。

## 技术栈

| 模块 | 技术 |
| --- | --- |
| 后端 | Python, FastAPI, Uvicorn, Pydantic Settings, SQLAlchemy |
| RAG | LlamaIndex, OpenAI-compatible API, DashScope Embedding/Rerank |
| 向量库 | Chroma 默认本地开发，Milvus / pgvector 可选 |
| 存储 | PostgreSQL, Redis |
| 前端 | Vue 3, TypeScript, Vite, Pinia, Tailwind CSS, lucide-vue-next |
| 评测 | pytest, RAGAS, retrieval ablation scripts |
| 可观测 | SSE Trace, Ingestion Report, optional Arize Phoenix |

## 目录结构

```text
app/
  agentic/       AgentController、Router、Query Transform、Workflow、Grounding
  auth/          注册、登录、JWT、权限依赖
  eval/          检索评估和 RAG 评测脚本
  ingest/        文档解析、增量索引、OCR 辅助
  llm/           统一 LLM Gateway
  retrieval/     BM25、缓存、Parent/Table/Auto-merging Context
  services/      会话记忆和长期记忆服务
  storage/       SQLAlchemy 模型和 parent chunk store
  tools/         Tool Registry、Tool Planner、Web Search、MCP 适配
frontend/        Vue 前端
docs/            架构、排障、实现说明
docker/          数据库初始化文件
tests/           单元测试和工作流测试
```

更详细的设计说明见 [docs/architecture.md](docs/architecture.md)。

## 快速开始

### 1. 环境要求

- Python 3.11+
- Node.js 18+
- Docker / Docker Compose
- DashScope API Key，或其他兼容 OpenAI 协议的 LLM / Embedding 服务

### 2. 配置环境变量

```bash
cp .env.example .env
```

至少需要配置：

```env
LLM_API_KEY=your-api-key
QWEN_LLM_API_KEY=your-api-key
JWT_SECRET_KEY=replace-with-a-long-random-secret
```

默认使用 Chroma 作为本地向量库。如果需要更接近生产环境的向量服务，可以切换到 Milvus。

### 3. 启动基础服务

```bash
docker compose up -d redis postgres
```

如需启动 Milvus：

```bash
docker compose --profile milvus up -d redis postgres etcd minio milvus
```

### 4. 安装后端依赖

推荐使用 `uv`：

```bash
uv sync
```

也可以使用 pip：

```bash
python -m pip install -e .
```

### 5. 启动后端

```bash
uv run uvicorn app.api:app --host 0.0.0.0 --port 8000 --reload
```

API 文档：

```text
http://localhost:8000/docs
```

### 6. 启动前端

```bash
cd frontend
npm install
npm run dev
```

访问：

```text
http://localhost:5173
```

## 创建管理员

在 `.env` 中配置管理员邀请码：

```env
ADMIN_INVITE_CODE=replace-with-admin-invite-code
```

注册时携带相同的 `admin_invite_code`，即可创建 admin 用户。不带邀请码注册的用户默认为普通 `user`。

## 文档入库

可通过前端知识库面板上传文档，也可以调用文档相关 API。

支持格式：

- `.txt`
- `.md`
- `.markdown`
- `.pdf`
- `.docx`
- `.html`
- `.htm`

入库后系统会生成解析、分块和索引报告，便于排查文档质量和召回效果。

## 常用配置

配置集中在 [app/config.py](app/config.py)，可通过 `.env` 覆盖。

重要配置：

- LLM / Embedding：`LLM_API_KEY`, `QWEN_LLM_API_KEY`, `LLM_API_BASE`, `LLM_MODEL`, `EMBEDDING_MODEL`
- 检索：`TOP_K`, `HIERARCHICAL_CHUNK_SIZES`, `RERANK_ENABLED`, `AUTO_MERGING_ENABLED`
- 存储：`VECTOR_STORE_BACKEND`, `CHROMA_DIR`, `MILVUS_URI`, `POSTGRES_URL`, `REDIS_HOST`
- 权限：`JWT_SECRET_KEY`, `ADMIN_INVITE_CODE`
- 可观测：`OBSERVABILITY_ENABLED`, `PHOENIX_ENDPOINT`
- 工具和长期记忆：`WEB_SEARCH_ENABLED`, `MCP_SERVER_URL`, `MEM0_ENABLED`

## 测试

运行测试：

```bash
uv run pytest
```

运行 RAG Workflow 相关测试：

```bash
uv run pytest tests/test_rag_workflow.py -v
```

评测脚本位于 `app/eval/` 和 `scripts/`。

## 可观测性

`/chat/stream` 会输出结构化 SSE 事件，覆盖路由、记忆加载、检索、Workflow 步骤、生成 token、最终引用和 trace。

可选开启 Phoenix：

```env
OBSERVABILITY_ENABLED=true
PHOENIX_ENDPOINT=http://localhost:6006/v1/traces
PHOENIX_PROJECT_NAME=weiquiz-agentic-rag
```

启动 Phoenix：

```bash
uv run phoenix serve
```

## 适合展示的面试讲法

可以重点展开：

- 为什么选择 RAG，而不是微调。
- 为什么普通向量检索不够，需要 Dense + BM25 + RRF + Rerank。
- 为什么要做 parent-child chunk 和 auto-merging。
- Agentic RAG 和普通 RAG 的区别。
- 多轮记忆如何兼顾上下文连续性和 token 成本。
- RAG 回答不准时如何通过 trace、source nodes、ingestion report 定位问题。
- 开源项目如何做配置、权限、文档、测试和工程化。

## Roadmap

- 文档级 ACL 检索过滤。
- 更安全的 SQL Tool：只读连接、表白名单、AST 校验、强制 limit、审计日志。
- 更系统的检索消融实验：dense-only、BM25-only、hybrid、rerank、auto-merging、HyDE、step-back。
- 更稳定的 OCR 和表格解析能力。
- 工具调用结果综合生成，而不是直接返回 raw payload。
- 扩展 Web Search、Memory Search、KB Search、SQL Query、MCP Client 等工具能力。
- 云端部署示例。

## 贡献

欢迎提交 Issue 和 Pull Request。贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 安全说明

请不要提交 `.env`、本地文档、索引文件、API Key 或生成的评测数据。仓库默认已通过 `.gitignore` 忽略这些内容。

如果发现安全问题，请优先私下联系维护者，不要直接公开漏洞细节。

## License

本项目采用 [MIT License](LICENSE) 开源。
