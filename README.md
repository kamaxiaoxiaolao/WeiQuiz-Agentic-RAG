# WeiQuiz Agentic RAG 项目说明

WeiQuiz 是一个面向企业知识库问答场景的 Agentic RAG 项目，用于学习、实践和展示主流 LLM 应用开发能力。项目覆盖文档解析、增量索引、混合检索、Rerank、父子块召回、Agentic RAG Workflow、会话记忆、流式响应、过程可观测与基础权限体系。

项目重点不是做一个简单的“向量搜索 Demo”，而是围绕真实 RAG 系统中常见的问题，构建一条可演示、可排查、可持续优化的企业知识库问答链路。

## 本地部署

### 1) 环境准备

- Python `3.11+`
- Node.js `18+`
- 包管理建议：`uv`
- Docker / Docker Compose，用于启动 Redis、PostgreSQL、Milvus、etcd、MinIO
- DashScope OpenAI-Compatible API Key，或其他兼容 OpenAI 协议的 LLM / Embedding 服务

### 2) 安装后端依赖

在项目根目录执行：

```bash
uv sync
```

如果使用 `pip`：

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -U pip
pip install -e .
```

### 3) 创建 `.env` 文件

项目根目录已有 `.env.example`，可复制为 `.env`：

```bash
copy .env.example .env
```

Linux / macOS：

```bash
cp .env.example .env
```

核心配置示例：

```env
# LLM / Embedding
LLM_API_KEY=replace-with-your-dashscope-api-key
LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.5-35b-a3b
EMBEDDING_MODEL=text-embedding-v1
EMBEDDING_BATCH_SIZE=20

# App paths
DOCS_DIR=data/docs
INDEX_DIR=data/index
AUDIT_DIR=data/audit

# Milvus
MILVUS_URI=http://localhost:19530
MILVUS_COLLECTION=wei_quiz_collection

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=1
REDIS_PASSWORD=

# PostgreSQL
POSTGRES_URL=postgresql+psycopg://postgres:postgres@localhost:5432/weiquiz
PARENT_STORE_ENABLED=true

# Retrieval
HIERARCHICAL_CHUNK_SIZES=2048,1024,256
TOP_K=5
AUTO_MERGING_ENABLED=true
AUTO_MERGING_THRESHOLD=0.5
AUTO_MERGING_MAX_CHARS=4000

# Long-term memory / Mem0，可选
MEM0_ENABLED=false
MEM0_MODE=platform
MEM0_API_KEY=
MEM0_SEARCH_LIMIT=5
MEM0_ASYNC_ADD=true
```

### 4) 启动基础依赖

```bash
docker compose up -d redis postgres etcd minio milvus
docker compose ps
```

端口说明：

- FastAPI：`8000`
- PostgreSQL：`5432`
- Redis：`6379`
- Milvus：`19530`
- Milvus health：`9091`
- MinIO API：`9000`
- MinIO Console：`9001`

### 5) 启动后端

```bash
uv run uvicorn app.api:app --host 0.0.0.0 --port 8000 --reload
```

访问：

- API 文档：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`

### 6) 启动前端

```bash
cd frontend
npm install
npm run dev
```

如果在 Windows PowerShell 中遇到 `npm.ps1` 执行策略问题，可以使用：

```bash
npm.cmd run dev
```

前端默认由 Vite 启动，访问终端输出的本地地址即可。

### 7) Docker 一键启动

也可以直接启动完整服务：

```bash
docker compose up -d --build
```

## 项目概览

### 核心能力

- FastAPI 后端 + Vue 3 前端 + Milvus 向量库。
- 文档扫描、SHA256 增量 diff、新增/更新/删除识别。
- PDF / Word / Markdown / Text / HTML 文档解析。
- Block 抽象：`title / text / list_item / table / image / unknown`。
- 文本清洗：页眉页脚、页码、目录、公式碎片、HTML 导航噪声等。
- Hierarchical Chunk：root / parent / leaf 三层节点。
- Leaf-only 向量化：只将 leaf chunk 写入 Milvus，parent/root 存入 PostgreSQL。
- Hybrid Retrieval：Milvus 向量检索 + BM25 + RRF 融合。
- DashScope `gte-rerank` 精排。
- Parent Context / Auto-merging：命中多个兄弟 leaf 时回取父级上下文。
- AgentController：统一调度闲聊、澄清、工具调用、RAG Workflow。
- Agentic RAG Workflow：Query Planning、子问题拆解、多跳检索、质量检查、Rewrite / Retry、Intermediate Synthesis、Grounding。
- 三层会话记忆：PostgreSQL 完整历史、Redis 最近窗口、SessionSummary 滚动摘要。
- Mem0 长期语义记忆适配层，可选开启，默认关闭。
- SSE 流式响应：推送 route、step、trace、chunk、result。
- 前端打字机效果：后端 chunk 进入前端队列后稳定逐步渲染。
- 基础账号体系：注册、登录、JWT 鉴权、admin/user 权限分离。
- Ingestion Report：记录入库成功失败、chunk 数、block 数、解析质量信号。

### 运行形态

```text
Vue 3 Frontend
  -> FastAPI API
  -> AgentController
  -> Agentic RAG Workflow
  -> LlamaIndex Retriever / Query Engine
  -> Milvus + PostgreSQL + Redis
```

## 关键设计点

### 1. AgentController 决策层

项目将“决策”和“执行”拆开。

AgentController 不直接做检索、不执行工具、不生成最终答案，而是统一判断请求应该进入哪条链路：

- `chitchat`：闲聊或记忆类轻量问题。
- `clarification`：问题信息不足，先反问用户。
- `tool_call`：需要工具调用。
- `rag_workflow`：进入知识库 RAG Workflow。

Controller 输出结构化 `AgentDecision`，包括：

- mode
- route
- memory_policy
- clarification
- tool_plan
- rag_strategy
- need_grounding
- max_retries

这样接口层不需要堆大量 if/else，后续扩展 Web Search、SQL、MCP、GraphRAG 时也更容易维护。

### 2. Hybrid Retrieval

项目不只使用向量检索，而是组合：

```text
Dense Vector Retrieval
  + BM25 Retrieval
  -> RRF Fusion
  -> Rerank
  -> Top Context
```

原因：

- 向量检索擅长语义相似，例如“登录凭证失效”召回“Token 过期”。
- BM25 擅长精确词匹配，例如 `401`、`403`、接口名、版本号、配置项。
- RRF 基于排名融合，不依赖 dense score 和 BM25 score 是否可比。
- Rerank 对候选 chunk 做 query-context 细粒度相关性判断。

### 3. 父子块与 Auto-merging

项目采用 hierarchical chunk 策略：

```text
root chunk
  -> parent chunk
    -> leaf chunk
```

设计原则：

- leaf chunk 用于检索，粒度小，匹配更精准。
- parent/root chunk 用于生成，保留更完整上下文。
- 向量库只存 leaf，减少向量冗余。
- PostgreSQL 存 root / parent / leaf 节点，支持 parent context 回取。

当同一个 parent 下多个 leaf 被命中，并超过阈值时，系统会自动回取 parent context，提高答案完整性。

### 4. 文档解析质量可观测

RAG 效果很大程度取决于文档入库质量。项目在 ingestion 阶段生成 `ingestion_report_latest.json`，记录：

- 新增、更新、删除文档数量。
- 成功与失败文档数量。
- 失败阶段和错误原因。
- section / chunk 长度统计。
- root / parent / leaf 节点统计。
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

配合 `data/audit/parsed_md/` 和 `data/audit/section_md/`，可以排查答案不准时问题到底出在解析、清洗、chunk、召回还是生成。

### 5. 会话记忆与摘要压缩

当前记忆系统分三层：

| 层级 | 作用 |
| --- | --- |
| PostgreSQL | 保存完整会话历史，支持审计和回放 |
| Redis / ChatMemoryBuffer | 保存最近窗口，低延迟注入短期上下文 |
| SessionSummary | 将滑出窗口的旧消息压缩成滚动摘要 |

回答前构建 prompt memory：

```text
SessionSummary
  + recent messages
  + optional Mem0 long-term memories
```

回答后：

- 用户与助手消息写入 PostgreSQL。
- 最近窗口写入 Redis。
- 超过阈值后后台触发摘要压缩。
- 符合长期记忆门控时，异步写入 Mem0。

### 6. SSE 流式输出与前端打字机效果

后端 `/chat/stream` 使用 `StreamingResponse(media_type="text/event-stream")` 推送事件：

- `route`：路由与 Controller 决策。
- `status`：当前执行状态。
- `step`：Workflow 步骤事件。
- `trace`：完整 RAG trace。
- `chunk`：生成内容片段。
- `result`：最终来源、引用和 trace。
- `[DONE]`：流结束。

前端使用 `response.body.getReader()` + `TextDecoder` 解析 SSE，收到 `chunk` 后进入本地 typewriter queue，以固定节奏逐步渲染，避免浏览器或后端批量合并 chunk 时看起来“不流式”。

### 7. Grounding / Reflection

项目支持可选答案依据校验：

- `off`：关闭。
- `auto`：由 AgentController 判断是否需要。
- `reflection`：强制开启。

Grounding 会检查答案关键结论是否被 source nodes 支撑。由于会增加额外 LLM 调用，因此不默认强制所有问题开启。

## 核心流程

### 1) 文档入库链路

1. 扫描 `DOCS_DIR` 下的文档。
2. 计算 SHA256，与 `ingest_state.json` 对比。
3. 得到 `added / updated / deleted`。
4. 新增文档：解析、清洗、section 合并、hierarchical chunk、向量化入库。
5. 更新文档：先删除旧 doc_id 对应 chunk，再重新入库。
6. 删除文档：清理 Milvus 和 PostgreSQL parent store 中的旧节点。
7. 写入 ingestion report 和 audit markdown。
8. 更新 `ingest_state.json`。

### 2) RAG 问答链路

1. 用户调用 `POST /chat/stream`。
2. 后端解析 JWT，确认当前用户和会话归属。
3. AgentController 判断执行模式。
4. 如果需要澄清，直接返回澄清问题。
5. 如果是闲聊或记忆问题，走轻量回答链路。
6. 如果进入 RAG Workflow：
   - Query Planning
   - 子问题拆解 / HyDE / Step-back
   - Hybrid Retrieval
   - Rerank
   - Quality Check
   - Rewrite / Retry
   - Intermediate Synthesis
   - Final Generation
   - Optional Grounding
7. SSE 实时推送步骤、trace 和 chunk。
8. 保存会话消息、source nodes、citations、trace。
9. 后台触发摘要压缩与可选长期记忆写入。

### 3) 答案不准排查链路

排查顺序：

```text
最终答案
  -> source nodes
  -> rerank 后 top_k
  -> hybrid 候选集
  -> chunk 内容
  -> audit markdown
  -> ingestion report
  -> ingest_state / doc_id
```

对应文档：

```text
docs/rag-troubleshooting-guide.md
```

## API 速览

### 认证

- `POST /auth/register`：注册用户。
- `POST /auth/login`：登录并返回 Bearer Token。
- `GET /auth/me`：获取当前用户信息。

### 聊天

- `POST /chat/stream`：主聊天入口，SSE 流式响应。
- `POST /query`：单轮知识库问答接口。

说明：当前聊天主入口统一为 `/chat/stream`。

### 会话

- `POST /sessions`：创建会话。
- `GET /sessions`：查询当前用户会话列表。
- `GET /sessions/{session_id}/messages`：查询当前用户指定会话消息。
- `PUT /sessions/{session_id}/title`：更新会话标题。
- `DELETE /sessions/{session_id}`：删除当前用户会话。

### 文档

- `POST /documents/upload`：上传文档并触发后台增量入库。
- `GET /documents/jobs/{job_id}`：查询入库任务状态。
- `GET /documents`：查看文档和最新 ingestion report。

文档管理接口需要管理员权限。

### 调试

- `GET /debug/memory/{session_id}`：查看当前会话记忆状态。
- `POST /debug/memory/{session_id}/compress`：手动触发摘要压缩。

## 目录与架构

```text
app/
  api.py                         FastAPI 入口、SSE、认证接入、聊天/文档/会话接口
  config.py                      全局配置
  rag_milvus.py                  LlamaIndex + Milvus + BM25 + RRF + Rerank
  metadata_schema.py             标准化 metadata 与 source node payload
  ui.py                          Streamlit 调试 UI

  agentic/
    controller.py                AgentController 决策层
    router.py                    Query Router
    hyde.py                      HyDE 查询转换
    step_back.py                 Step-back 查询转换
    retrieval_quality.py         检索质量评估
    rag_workflow.py              Agentic RAG Workflow 数据结构
    llama_workflow.py            LlamaIndex Workflow + step streaming
    node_synthesizer.py          最终答案流式生成
    grounding.py                 Grounding / Reflection

  ingest/
    sync.py                      SHA256 diff 与 ingest_state
    document_parser.py           文档解析、Block 抽象、清洗、audit
    milvus_loader.py             增量入库、hierarchical chunk、report
    ocr_single_pdf.py            OCR 单文件工具
    ocr_unstructured_pdf.py      unstructured OCR 工具

  retrieval/
    parent_context.py            Parent context 回取
    auto_merging_context.py      Auto-merging parent context

  services/
    memory_service.py            PostgreSQL + Redis + SessionSummary 记忆服务
    long_term_memory_service.py  Mem0 长期语义记忆适配层

  storage/
    auth_models.py               用户、会话、消息、摘要 ORM
    parent_store.py              PostgreSQL parent/root/leaf chunk store

  tools/
    registry.py                  Tool Registry
    planner.py                   Function Calling Tool Planner
    web_search.py                Web Search Adapter 占位

  eval/
    eval_retrieval.py            检索评估脚本
    eval_ragas.py                RAGAS 评估尝试
    prepare_squad_eval.py        SQuAD 数据准备
    prepare_beir_scifact.py      BEIR SciFact 数据准备

frontend/
  src/
    components/
      ChatPanel.vue              聊天、SSE、打字机、RAG step 展示
      DebugPanel.vue             Trace / Source node 调试面板
      KnowledgeSidebar.vue       文档入库与任务状态
      SessionList.vue            会话列表
```

## 技术栈

- 后端：Python、FastAPI、Pydantic、SQLAlchemy、Uvicorn。
- RAG 框架：LlamaIndex。
- LLM：DashScope OpenAI-Compatible API。
- 向量与检索：Milvus、BM25、QueryFusionRetriever、RRF、DashScope Rerank。
- 数据库与缓存：PostgreSQL、Redis。
- 文档解析：unstructured、pypdf、DOCX XML、HTMLParser、PyMuPDF。
- 前端：Vue 3、Vite、TypeScript、marked、lucide-vue-next。
- 评测：RAGAS、SQuAD / BEIR 数据准备脚本、检索评估脚本。
- 工具扩展：Tool Registry、Function Calling Planner、Web Search Adapter 占位。

## 当前已实现与规划边界

### 已实现

- AgentController 决策层。
- Hybrid Retrieval + RRF + Rerank。
- Hierarchical Chunk + Leaf-only Indexing。
- Parent Context / Auto-merging。
- Agentic RAG Workflow。
- Query Planning、子问题拆解、Rewrite / Retry。
- 可选 Grounding / Reflection。
- PostgreSQL + Redis + SessionSummary 三层记忆。
- Mem0 适配层与可选长期记忆门控。
- SSE 流式输出与前端打字机效果。
- 文档解析质量报告与 audit 文件。
- JWT 登录认证、基础 RBAC、用户会话隔离。

### 规划中 / 预留扩展

- 完整 MCP Client / Server 工具生态。
- 真实 Web Search 工具接入。
- SQL Assistant 闭环。
- GraphRAG。
- 文档级 ACL 检索过滤。
- 稳定的 RAGAS 自动化评测闭环。
- 更完整的 Mem0 查看、删除、更新接口。
- 多 Agent 协作。

## 相关文档

- [项目总结](docs/project-summary.md)
- [AgentController 设计说明](docs/agent-controller-design.md)
- [Mem0 长期语义记忆说明](docs/mem0-long-term-memory.md)
- [RAG 故障排查手册](docs/rag-troubleshooting-guide.md)
- [企业级 Agentic RAG 项目计划](agent/enterprise_agentic_rag_project_plan.md)
