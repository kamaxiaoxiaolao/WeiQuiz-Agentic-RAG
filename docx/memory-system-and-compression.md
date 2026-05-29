# WeiQuiz 记忆系统与压缩机制

## 1. 这套记忆系统解决什么问题

RAG 系统默认只能回答当前问题和当前检索到的知识库内容。如果用户连续多轮对话，例如“刚才我说了什么”“按前面的方案继续”“请记住我的项目目标”，系统就需要会话记忆。

WeiQuiz 当前的记忆系统解决的是 **同一个 session 内的多轮连续性**：

- 保存完整聊天历史，避免历史消息丢失。
- 控制 prompt 中注入的历史长度，避免 token 无限增长。
- 把旧消息压缩成摘要，保留长期上下文。
- 支持普通记忆追问走轻量路径，不触发完整 RAG 检索链路。
- 提供 debug 接口验证记忆是否真的写入、压缩和注入。

当前实现重点是 **Session 级短期记忆 + 滚动摘要压缩**。跨 session 的长期语义记忆、Mem0、Graph Memory 暂未接入，后续作为高级阶段演进。

## 2. 为什么不能只用 ChatMemoryBuffer

LlamaIndex 的 `ChatMemoryBuffer` 适合做短期窗口，但它不是完整记忆系统。

它的特点是：

- 可以保存最近对话。
- `memory.get()` 时会根据 token limit 裁剪历史。
- 裁剪单位接近 message-level，不是严格的一轮问答。
- 不会自动生成摘要。
- 不会告诉我们哪些消息刚刚被滑出窗口。
- 不适合作为完整历史的唯一存储。

所以项目没有把 `ChatMemoryBuffer` 当成最终存储，而是把它降级为 **Redis recent window 缓存**。

更规范的做法是：

```text
PostgreSQL = 完整历史事实源
Redis / ChatMemoryBuffer = 最近窗口缓存
SessionSummary = 旧消息压缩摘要
MemoryContext = 最终注入 prompt 的记忆对象
```

## 3. 当前已实现的三层结构

### 3.1 PostgreSQL 完整历史

完整消息存储在 `chat_messages` 表中。

核心字段：

- `id`
- `session_id`
- `owner_user_id`
- `role`
- `content`
- `status`
- `metadata_json`
- `created_at`

每一轮对话会写入两条消息：

```text
user: 用户输入
assistant: 最终回答
```

assistant 消息的 `metadata_json` 会保存：

- `sources`
- `citations`
- `route`
- `trace`

这样历史会话展示不依赖 Redis，即使 Redis 过期或重启，完整聊天记录仍然可以从 PostgreSQL 恢复。

### 3.2 Redis Recent Window

Redis 中保存的是 LlamaIndex `ChatMemoryBuffer` 的序列化结果。

它的作用不是保存完整历史，而是加速读取最近几轮上下文。

当前 prompt 注入默认取最近 3 轮，即最多 6 条消息：

```text
user
assistant
user
assistant
user
assistant
```

如果 Redis recent window 丢失，`MemoryService.build_context()` 会从 PostgreSQL 回填最近消息。

### 3.3 SessionSummary 滚动摘要

旧消息压缩后写入 `session_summaries` 表。

核心字段：

- `session_id`
- `owner_user_id`
- `summary`
- `covered_until_message_id`
- `covered_message_count`
- `version`
- `created_at`
- `updated_at`

其中最关键的是 `covered_until_message_id`。

它表示：

```text
当前 summary 已经覆盖到哪一条 chat_messages.id
```

下一次压缩时，只会摘要新的、尚未覆盖的旧消息，避免重复压缩同一段历史。

## 4. 一次 /chat/stream 请求里的记忆链路

当前链路如下：

```text
用户请求 /chat/stream
  -> 校验 session 归属
  -> Router 判断意图
  -> MemoryService.load(session_id)
  -> build_context()
      -> 读取 Redis recent messages
      -> Redis 缺失时从 PostgreSQL 回填最近消息
      -> 读取 SessionSummary
      -> 构造 MemoryContext
  -> 根据意图进入不同路径
      -> CHITCHAT: Lightweight Chat
      -> 知识库问题: Agentic RAG Workflow
  -> 生成回答
  -> append_exchange_with_metadata()
      -> 写入 PostgreSQL 完整历史
      -> 写入 Redis recent buffer
  -> BackgroundTasks 触发后台摘要压缩
```

最终传给 LLM 的不是完整历史，而是：

```text
历史摘要
+ 最近几轮对话
+ 当前问题
+ RAG 检索证据
```

普通记忆追问不会走 RAG，而是走轻量路径：

```text
记忆类规则 Router
  -> CHITCHAT
  -> Lightweight Chat
  -> MemoryContext
  -> 流式回答
```

## 5. 滑动窗口怎么滑

项目里有两层“窗口”概念。

第一层是 LlamaIndex `ChatMemoryBuffer` 自身的 token window。

它在 `memory.get()` 时根据 token limit 动态裁剪，属于读取时裁剪。

第二层是项目自己实现的 recent window。

当前策略是：

```text
DEFAULT_MEMORY_CONTEXT_TURNS = 3
SUMMARY_RECENT_MESSAGES = 6
```

也就是：

- prompt 默认只注入最近 3 轮对话。
- 后台压缩后 Redis 只保留最近 6 条消息。
- 更旧的消息不直接放进 prompt，而是进入 SessionSummary。

注意：完整历史不会被删除，仍然保存在 PostgreSQL。

## 6. 摘要什么时候触发

当前摘要触发阈值：

```text
SUMMARY_TRIGGER_MESSAGES = 12
SUMMARY_RECENT_MESSAGES = 6
```

含义是：

```text
当某个 session 的完整消息数 > 12 时
  -> 保留最近 6 条原文消息
  -> 更旧且尚未被 summary 覆盖的消息进入摘要
```

压缩流程：

```text
读取 PostgreSQL 完整消息
  -> recent = 最后 6 条
  -> evicted = recent 之前的旧消息
  -> 根据 covered_until_message_id 过滤已摘要消息
  -> LLM 生成新的 rolling summary
  -> 更新 session_summaries
  -> 裁剪 Redis ChatMemoryBuffer 到 recent
```

摘要 prompt 的原则：

- 保留用户目标、已确认结论、约束、未完成问题。
- 不写 RAG trace、chunk、HyDE、Step-back 等内部执行细节。
- 不新增对话中没有的事实。
- 用简洁中文生成可注入 prompt 的摘要。

## 7. 为什么摘要压缩要放后台

最初可以把压缩放在回答结束后同步执行，但这样有明显问题：

```text
回答完成
  -> 写消息
  -> 调摘要 LLM
  -> 更新 summary
  -> 返回响应
```

摘要 LLM 一旦慢、超时或失败，就会拖慢用户聊天体验。

当前已经改成后台压缩：

```text
回答完成
  -> 消息先写 PostgreSQL
  -> Redis recent buffer 先保存
  -> SSE 响应结束
  -> FastAPI BackgroundTasks 后台压缩
```

这个设计的取舍是：

- 完整消息写入是强路径，必须优先保证。
- 摘要压缩是弱一致后台任务，可以稍后完成。
- 后台压缩失败不会丢消息，因为 PostgreSQL 完整历史还在。
- 同一个 session 加了进程内压缩锁，避免并发重复压缩同一段历史。

当前使用的是 FastAPI `BackgroundTasks`。它足够支撑开发期和演示。

如果进入生产环境，可以升级为：

```text
Celery / RQ / Dramatiq / Arq
+ retry
+ task status
+ dead letter
+ metrics
```

## 8. Lightweight Chat Fast Path

为了避免简单记忆问题误入完整 RAG 链路，项目增加了记忆类规则 fast path。

这类问题会直接判定为 `CHITCHAT`：

```text
请记住...
帮我记住...
记一下...
我刚刚说了什么？
我前面让你记住了什么？
总结一下我们刚才聊的内容。
我们刚才说到哪了？
```

命中后链路是：

```text
rule router
  -> intent = chitchat
  -> query_strategy = chitchat
  -> lightweight_chat
  -> 注入 MemoryContext
```

这类问题不会触发：

- RAG 检索
- rerank
- quality check
- query rewrite
- HyDE
- Step-back
- 子问题分解

但为了避免误判，包含这些词的问题仍然不会被记忆规则截走：

```text
知识库
文档
资料
报告
```

例如：

```text
知识库里总结一下前面的文档内容
```

这类问题仍然交给 RAG 路由。

## 9. 调试与验收

项目新增开发期调试接口：

```http
GET /debug/memory/{session_id}
```

需要登录态 Bearer Token，并且只能查看自己的 session。

返回内容包括：

- `postgres.message_count`
- `postgres.recent_messages`
- `summary.exists`
- `summary.covered_until_message_id`
- `summary.covered_message_count`
- `redis.memory_message_count`
- `prompt_context.used_summary`
- `prompt_context.recent_messages`
- `prompt_context.session_summary`

推荐验收流程：

连续使用同一个 session 发送：

```text
请记住，我正在测试 WeiQuiz 的记忆系统。
请记住，我的目标是面试能讲清楚项目。
请记住，当前记忆系统有 PostgreSQL、Redis、SessionSummary 三层。
我刚刚说了什么？
我前面让你记住了什么？
总结一下我们刚才聊的内容。
我们刚才说到哪了？
```

然后调用：

```bash
curl -H "Authorization: Bearer 你的token" ^
  http://127.0.0.1:8000/debug/memory/你的session_id
```

预期结果：

```json
{
  "postgres": {
    "message_count": 14
  },
  "summary": {
    "exists": true,
    "covered_until_message_id": 8
  },
  "redis": {
    "memory_message_count": 6
  },
  "prompt_context": {
    "used_summary": true
  }
}
```

如果看到类似结果，说明：

```text
完整历史进入 PostgreSQL
旧消息进入 SessionSummary
Redis 只保留最近窗口
下一轮 prompt 会注入 summary + recent messages
```

## 10. 当前实现够不够

对于当前阶段，已经够用。

它完成的是：

- session 级完整历史保存
- session 级短期窗口
- session 级滚动摘要
- 后台异步压缩
- 记忆类 fast path
- debug 可观测性

它还没有完成：

- 跨 session 长期记忆
- 用户画像
- 偏好学习
- Mem0 接入
- 结构化 / 图记忆
- 长期记忆检索注入
- 记忆冲突处理
- 记忆删除与隐私治理

这些属于后续高级阶段，不应该在当前阶段混进来。

## 11. 面试怎么回答

如果面试官问：

> 你的记忆系统是怎么设计的？

可以这样回答：

```text
我的项目没有简单把 ChatMemoryBuffer 当成完整记忆，而是做了三层设计。

第一层是 PostgreSQL 完整历史，保存每一条 user / assistant 消息，以及 assistant 的 route、trace、sources、citations 等元数据，这是事实源。

第二层是 Redis recent window，用 LlamaIndex ChatMemoryBuffer 保存最近几轮对话，用于低延迟构造上下文。如果 Redis 丢失，可以从 PostgreSQL 回填最近消息。

第三层是 SessionSummary。超过阈值后，我保留最近 6 条消息，把更旧且尚未被摘要覆盖的消息滚动压缩成 summary，并用 covered_until_message_id 记录摘要覆盖边界，避免重复压缩。

最终生成时注入的是“历史摘要 + 最近对话 + 当前问题 + RAG 证据”，而不是完整历史。这样既控制 token，又保留多轮上下文。
```

如果面试官追问：

> 为什么摘要压缩要异步？

可以回答：

```text
因为摘要压缩本质上又是一次 LLM 调用，如果放在同步回答链路里，会拖慢 SSE 响应的尾延迟。

所以我把完整消息写入作为强路径，先保证 PostgreSQL 和 Redis recent buffer 写入成功；然后用 FastAPI BackgroundTasks 在响应结束后做 summary compression。

压缩失败不会影响本轮回答，因为完整历史还在 PostgreSQL。后续生产化可以把 BackgroundTasks 升级成 Celery 或 RQ，增加重试和任务状态。
```

如果面试官追问：

> 怎么证明你的记忆真的压缩了？

可以回答：

```text
我做了一个开发期 debug 接口 /debug/memory/{session_id}。

它可以看到 PostgreSQL 完整消息数、Redis recent window 消息数、SessionSummary 是否存在、covered_until_message_id，以及下一轮真正注入 prompt 的 MemoryContext。

我会连续发送 7 轮对话，然后观察完整消息数持续增长，Redis recent window 稳定在 6 条左右，summary.exists 变成 true，prompt_context.used_summary 变成 true。
```

## 12. 后续演进路线

当前阶段不要继续扩展太多，先把 session 级记忆讲清楚。

后续可以按这个顺序演进：

```text
Stage 1: Session short-term memory
  已完成：PostgreSQL full history + Redis recent buffer

Stage 2: Rolling summary compression
  已完成：SessionSummary + background compression

Stage 3: Long-term semantic memory
  待实现：从对话中抽取稳定事实、偏好、决策、todo

Stage 4: Memory retrieval
  待实现：按 user_id + query 检索长期记忆并注入 prompt

Stage 5: Mem0 adapter
  待实现：用 Mem0 承担长期记忆 add/search/update/delete

Stage 6: Graph memory
  待实现：把用户、项目、模块、决策抽象成实体关系图
```

当前项目最应该先完成的是：

```text
记忆系统端到端验收
-> 写入项目计划和简历描述
-> 回到 RAG 主线继续做数据预处理 / 检索优化
```
