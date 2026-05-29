# WeiQuiz 多轮对话与 SSE 实现说明（项目内实现版）

本文总结当前代码库里“多轮对话（会话记忆 + 会话列表/切换）”与“SSE 流式输出（含多事件 status/chunk/result）”的实现细节与关键设计点。

***

## 1. 总体架构（RAG + 多轮 + UI）

- 后端：FastAPI
  - 负责加载 RAG 组件、提供 `/query`、`/chat`、`/chat/stream`、会话管理接口
  - 代码入口：[api.py](file:///d:/study/pycharm/workspace/bigModel/app/api.py)
- RAG：LlamaIndex + Milvus + BM25 + RRF 融合 + DashScope Rerank
  - 初始化与组件构建：[rag\_milvus.py](file:///d:/study/pycharm/workspace/bigModel/app/rag_milvus.py)
- 前端：Streamlit
  - 会话列表展示、会话切换、流式渲染、溯源展示：[ui.py](file:///d:/study/pycharm/workspace/bigModel/app/ui.py)
- 会话持久化：Redis（memory）+ ZSET（会话列表索引）
  - Redis key 约定在 [api.py](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L16-L42)

***

## 2. RAG 组件构建与复用（避免每次请求重建）

### 2.1 启动期初始化（lifespan）

后端在 FastAPI lifespan 中一次性构建并缓存 RAG 组件：

- `index / retriever / reranker / query_engine` 挂载到 `app.state`
- 代码：[api.py:L44-L83](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L44-L83)

这样 `/query` 和 `/chat` 不需要每次重新加载 Milvus/Docstore、重新构建 BM25 索引或 Reranker。

### 2.2 为什么不用 `index.as_chat_engine(query_engine=...)`

本项目最终选择 `CondensePlusContextChatEngine.from_defaults(retriever=..., node_postprocessors=[reranker])` 来构建 ChatEngine：

- 代码：[rag\_milvus.py:L87-L92](file:///d:/study/pycharm/workspace/bigModel/app/rag_milvus.py#L87-L92)
- 原因：`VectorStoreIndex.as_chat_engine(chat_mode="condense_plus_context")` 内部会改走 `self.as_retriever()` 的默认路径，容易绕开自定义的 fusion retriever / rerank（之前已验证过这一点）。

### 2.3 Hybrid Retriever 关键参数

- Vector retriever：`index.as_retriever(similarity_top_k=4)`
- BM25 retriever：`BM25Retriever.from_defaults(nodes=all_nodes, similarity_top_k=4)`（节点来自 docstore）
- 融合：`QueryFusionRetriever(..., mode="reciprocal_rerank", similarity_top_k=10)`
- 注意：`use_async=False`
  - 代码：[rag\_milvus.py:L60-L66](file:///d:/study/pycharm/workspace/bigModel/app/rag_milvus.py#L60-L66)
  - 原因：在 FastAPI/uvicorn 环境下，Milvus async client 容易触发 `Event loop is closed`（你们遇到过）。关闭 async 是当前实现里最稳的服务化策略。

***

## 3. 多轮对话实现细节（session\_id → memory → chat\_engine）

### 3.1 请求模型

- `/chat` 与 `/chat/stream` 统一使用 `ChatRequest`：
  - `session_id`：会话唯一标识（UI 生成 uuid）
  - `message`：用户输入
  - 代码：[api.py:L89-L92](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L89-L92)

### 3.2 Memory 的加载与写回（Redis 优先，内存兜底）

**加载逻辑**（`/chat` 和 `/chat/stream` 都类似）：

1. 先尝试 Redis：`_load_memory_from_redis(redis, session_id)`
2. Redis 不可用或读失败 → 使用进程内 `memory_buffers` 兜底
3. 新会话 → `ChatMemoryBuffer.from_defaults(token_limit=4096)`\
   代码参考：

- Redis 读写 helper：[api.py:L31-L41](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L31-L41)
- `/chat` 的 memory 选择逻辑：[api.py:L126-L163](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L126-L163)

**写回逻辑**：

- 对话完成后写回：
  - &#x20;` _save_memory_to_redis(...)`（带 TTL：`chat_msg_ttl`）
  - `_touch_session(...)`（更新会话索引 ZSET + TTL：`session_list_ttl`）\
    代码：[api.py:L167-L174](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L167-L174)

### 3.3 会话列表索引（为什么要有 sessions ZSET）

- `rag:chat:sessions`：ZSET，member=session\_id，score=最后活跃时间戳
- 用途：
  - UI 展示历史会话（最近活跃排序）
  - 删除/清理会话定位
  - 重启后仍可恢复会话入口（不靠进程内存）\
    实现：
- `_sessions_key/_touch_session/_remove_session`：[api.py:L16-L28](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L16-L28)
- `GET /sessions`：[api.py:L191-L197](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L191-L197)
- `DELETE /sessions/{session_id}`：[api.py:L200-L206](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L200-L206)

### 3.4 UI 的会话切换

UI 在侧边栏：

- 拉 `/sessions` 获取 session 列表
- radio 切换后调用 `/sessions/{session_id}/messages` 回放历史消息
- 删除会话调用 `DELETE /sessions/{session_id}`\
  代码：[ui.py:L158-L207](file:///d:/study/pycharm/workspace/bigModel/app/ui.py#L158-L207)

**历史消息回放接口**：

- `GET /sessions/{session_id}/messages`：从 Redis memory 还原出 chat\_history
- 代码：[api.py:L209-L228](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L209-L228)

***

## 4. SSE 流式输出实现细节（/chat/stream）

### 4.1 后端 SSE 端点

- 路由：`POST /chat/stream`
- 返回：`StreamingResponse(..., media_type="text/event-stream")`\
  代码：[api.py:L231-L309](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L231-L309)

### 4.2 当前 SSE 事件协议（实现版）

后端 `gen()` 里依次输出：

1. `event: status`

- `data: 正在加载会话与上下文...`
- `data: 正在通过本地知识库进行检索...`
- `data: 检索完成，开始生成回答...`\
  代码：[api.py:L258-L272](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L258-L272)

1. `event: chunk`

- `data: <token>`（对 token 进行换行转义 `\n -> \\n`，避免 SSE 断行解析异常）\
  代码：[api.py:L274-L278](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L274-L278)

1. `event: result`

- `data: {"source_nodes":[...]}`（JSON，溯源数据一次性发送）\
  代码：[api.py:L288-L305](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L288-L305)

1. 结束标记

- `data: [DONE]`\
  代码：[api.py:L306-L307](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L306-L307)

### 4.3 前端 SSE 解析与渲染

UI 通过 `requests.post(..., stream=True).iter_lines()` 解析 SSE：

- 读取 `event:` 行，记录 `current_event`
- 读取 `data:` 行：
  - `status` → 在 `status_placeholder` 里显示系统状态
  - `chunk` → 还原换行 `\\n -> \n`，增量渲染正文（带光标效果）
  - `result` 或含 `"source_nodes"` 的 JSON → 解析溯源（不渲染进正文）
  - `[DONE]` → 结束\
    解析函数：[ui.py:L75-L127](file:///d:/study/pycharm/workspace/bigModel/app/ui.py#L75-L127)

渲染循环（status + chunk + end）：[ui.py:L236-L300](file:///d:/study/pycharm/workspace/bigModel/app/ui.py#L236-L300)

### 4.4 溯源展示与持久化

- UI 将本次回答的 `source_nodes` 存入 `st.session_state.messages`，保证刷新后仍可展开查看引用来源
- 展示逻辑：渲染历史消息时如果包含 `source_nodes` 则展示 expander\
  代码：
- 历史渲染：[ui.py:L213-L235](file:///d:/study/pycharm/workspace/bigModel/app/ui.py#L213-L235)
- 本轮回答写入消息列表：[ui.py:L295-L300](file:///d:/study/pycharm/workspace/bigModel/app/ui.py#L295-L300)

***

## 5. 当前实现的边界与已规避问题（关键经验）

- **Milvus async + uvicorn reload 容易 event loop 崩溃**
  - 通过 `QueryFusionRetriever(use_async=False)` 规避
  - 代码：[rag\_milvus.py:L60-L66](file:///d:/study/pycharm/workspace/bigModel/app/rag_milvus.py#L60-L66)
- **ChatEngine 必须显式使用自定义 retriever/reranker**
  - 用 `CondensePlusContextChatEngine.from_defaults(retriever=..., node_postprocessors=[reranker])` 绑定自定义链路
  - 代码：[rag\_milvus.py:L87-L92](file:///d:/study/pycharm/workspace/bigModel/app/rag_milvus.py#L87-L92)
- **SSE token 换行必须转义**
  - 后端 `\n -> \\n`，前端再还原
  - 代码：后端 [api.py:L275-L278](file:///d:/study/pycharm/workspace/bigModel/app/api.py#L275-L278)，前端 [ui.py:L121-L124](file:///d:/study/pycharm/workspace/bigModel/app/ui.py#L121-L124)

***

## 6. 下一步可演进方向（与当前实现最贴合）

- 将 SSE 协议统一为 `status/token/end`（当前是 `status/chunk/result + [DONE]`），减少歧义并更标准化。
- 增加“断连/取消生成”能力：UI 停止按钮 + 后端检测断连，及时停止检索与生成，避免资源浪费。
- `/sessions` 返回结构升级：返回 `{session_id, last_active_ts}`，UI 可展示“最近活跃时间”。

如果你希望我把当前的 `status/chunk/result/[DONE]` 协议，完整升级成 `status/token/end` 并保持向后兼容，我可以按你现有代码结构给出一份“一次性替换的完整实现代码”（你照着手写即可）。
