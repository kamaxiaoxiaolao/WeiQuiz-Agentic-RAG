# WeiQuiz 记忆系统主流实现说明

本文用于说明 WeiQuiz 当前记忆系统的主流架构、请求流程和可对外讲清楚的实现口径。

## 1. 总体架构

WeiQuiz 采用分层记忆架构，把“快、准、可追溯、可跨会话”拆给不同存储层负责：

| 层级 | 作用 | 存储/实现 | 进入 Prompt 的方式 |
| --- | --- | --- | --- |
| 短期记忆 | 保留最近几轮原文，解决连续追问和代词指代 | Redis + LlamaIndex `ChatMemoryBuffer`，Redis 不可用时降级到进程内存 | `【最近对话】` |
| 中期记忆 | 把长会话压缩为滚动摘要，避免上下文窗口爆掉 | PostgreSQL `session_summaries` | `【历史摘要】` |
| 长期记忆 | 记录跨会话稳定事实、偏好、目标、项目背景 | Mem0 适配器，可切 platform/local | `【长期记忆】` |
| 完整历史 | 审计、回放、重建摘要、调试 | PostgreSQL `chat_messages` | 不直接全量进入 Prompt |
| 元数据 | 前端展示来源、引用、路由和 trace | Redis metadata + PostgreSQL assistant metadata | 不作为模型事实输入 |

这种做法接近主流 Agent/RAG 记忆系统：最近原文保证细节，摘要保证长上下文连续性，长期记忆负责个性化和跨会话连续性，完整历史负责可追溯。

## 2. 一次对话的记忆流程

1. 用户发起 `/chat/stream`，系统先确认会话归属。
2. `AgentController` 识别意图，并生成 `MemoryPolicy`：
   - 普通闲聊和追问会使用最近对话、摘要、长期记忆。
   - 工具调用和 RAG 问答也可以使用记忆，但长期记忆 top-k 更保守。
3. `MemoryService.load(session_id)` 加载短期记忆：
   - 优先 Redis。
   - Redis 没有则使用进程内缓存。
   - 都没有则创建新的 `ChatMemoryBuffer`。
4. `MemoryService.build_context(...)` 按 `MemoryPolicy` 构建 Prompt 记忆：
   - `use_recent_messages=False` 时不会注入最近对话。
   - `use_session_summary=False` 时不会读取或注入摘要。
   - 这让不同意图可以控制记忆开销和污染风险。
   - 如果 Redis/进程内窗口为空，会从 PostgreSQL 最近消息恢复短期窗口，并回填缓存。
5. 如果策略允许长期记忆，`LongTermMemoryService.search(user_id, query)` 检索跨会话记忆。
6. RAG/闲聊生成回答时，`format_memory_context` 将长期记忆、历史摘要、最近对话拼成结构化 Prompt 段落。
7. 回答完成后，系统双写：
   - Redis 短期窗口：保存最近原文，提升下一轮响应速度。
   - PostgreSQL 完整历史：保存 user/assistant 消息和元数据。
8. 后台触发摘要压缩：
   - 消息数超过阈值后，保留最近 6 条原文。
   - 更早的消息被 LLM 压缩进 `SessionSummary`。
   - `covered_until_message_id` 记录摘要边界，避免重复压缩。
   - 摘要输入有总字符预算，避免一次压缩把过长历史全部塞给模型。
   - 压缩后同步裁剪 Redis metadata，保证最近消息和 sources/citations/trace 不错位。
9. 长期记忆写入采用门控：
   - 只写用户明确要求“记住”的内容，或稳定的目标、偏好、项目事实、技术栈等。
   - 丢弃失败回答、无依据回答、临时问答。
   - 内容会截断并过滤低价值消息，避免把噪声永久化。

## 3. 为什么不把所有历史都塞进 Prompt

主流实现不会直接把完整历史塞给模型，原因有三个：

1. 成本高：长会话会快速消耗 token，降低响应速度。
2. 噪声高：历史里有闲聊、失败回答、无关检索 trace，直接注入会干扰回答。
3. 不安全：旧事实可能过期，长期记忆需要门控、检索和冲突处理。

所以 WeiQuiz 采用“最近原文 + 滚动摘要 + 长期语义检索”的组合：细节靠最近窗口，长期脉络靠摘要，跨会话稳定信息靠长期记忆。

## 4. 关键实现点

### 4.1 短期记忆

核心文件：`app/services/memory_service.py`

`ChatMemoryBuffer` 保存最近对话。Redis 是热缓存和跨进程共享层，进程内 dict 是本地开发降级方案。每轮回答结束后调用 `append_exchange_with_metadata`，同时写入用户消息、助手消息和展示元数据。

### 4.2 滚动摘要

当 PostgreSQL 中某个会话消息数超过 `SUMMARY_TRIGGER_MESSAGES=12` 时，后台压缩旧消息：

```text
完整历史:  [旧消息 ...] [最近 6 条]
处理方式:  旧消息 -> LLM 摘要
保留方式:  最近 6 条 -> Redis 短期窗口
```

摘要写入 `session_summaries`，并用 `covered_until_message_id` 记录已经覆盖到哪条消息。下一次压缩只处理新的旧消息。

### 4.3 长期记忆

核心文件：`app/services/long_term_memory_service.py`

长期记忆不是每轮都写。写入门控只接受这类内容：

- 显式记忆：`请记住`、`帮我记住`、`remember`
- 用户画像：`我的偏好`、`我喜欢`、`我不喜欢`、`我习惯`
- 稳定目标：`我的目标`、`我希望`、`我正在`
- 项目事实：`我的项目`、`项目是`、`系统是`、`架构是`、`技术栈`
- 后续偏好：`以后`、`以后都`、`下次请`

失败回答、无依据回答和临时信息不会写入长期记忆。

## 5. 可以这样对外讲

> 我们的记忆系统不是简单地把历史消息拼到 Prompt 里，而是分层管理。短期层用 Redis + ChatMemoryBuffer 保存最近几轮原文，保证连续追问能接上；中期层用 PostgreSQL 保存滚动摘要，把超过窗口的旧消息压缩成稳定上下文；长期层通过 Mem0 做跨会话语义记忆，只写入用户明确要求记住的偏好、目标和项目事实；完整历史仍然落 PostgreSQL，用于审计、回放和重建摘要。请求进来时，AgentController 会先决定 MemoryPolicy，然后 MemoryService 按策略组装最近对话和摘要，LongTermMemoryService 再按 user_id 检索相关长期记忆。回答结束后，系统双写 Redis 和 PostgreSQL，并在后台异步压缩摘要。这样既能保持多轮连续性，又能控制 token 成本和记忆污染。

## 6. 当前边界和后续方向

当前系统已经具备主流分层记忆能力，但还有三个可继续增强的方向：

1. 长期记忆冲突检测：例如用户偏好从“喜欢详细解释”变成“只要结论”，应更新旧记忆而不是并存。
2. 用户画像结构化：把领域、熟练度、回答风格偏好抽成结构化 profile。
3. 任务记忆：对长任务保存目标、计划、已完成步骤和待办，支持中断恢复。
