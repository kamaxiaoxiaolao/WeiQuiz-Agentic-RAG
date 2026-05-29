# Mem0 长期语义记忆设计说明

## 1. 为什么需要 Mem0

当前 WeiQuiz 已经有会话级记忆：

- PostgreSQL 保存完整聊天历史，用于回放、审计和重新压缩。
- Redis / ChatMemoryBuffer 保存最近窗口，用于短期上下文。
- SessionSummary 保存当前 session 中滑出窗口的滚动摘要。

这些能力解决的是“当前会话怎么记住上下文”。但它们不能很好解决跨 session 的长期用户偏好和背景事实，例如：

- 用户正在准备 Agentic RAG 面试。
- 用户希望回答偏工程实践。
- 用户项目是 WeiQuiz Agentic RAG。
- 用户要求以后回答不要太长。

Mem0 的定位是跨 session 的长期语义记忆层。它不替代 Redis，也不替代 PostgreSQL，而是补充“用户长期事实和偏好”的检索能力。

## 2. Mem0 和现有三层记忆的区别

| 层级 | 存什么 | 作用 | 是否注入 Prompt |
|---|---|---|---|
| PostgreSQL | 完整 user / assistant 消息 | 审计、回放、重新压缩 | 不直接全量注入 |
| Redis / ChatMemoryBuffer | 最近几轮消息 | 短期上下文窗口 | 注入最近窗口 |
| SessionSummary | 当前 session 的旧上下文摘要 | 控制 token，保留会话长期上下文 | 注入摘要 |
| Mem0 | 跨 session 的用户目标、偏好、项目背景、稳定事实 | 长期语义记忆 | 按 query 检索 top-k 后注入 |

核心区别：

```text
Redis 是短期窗口。
SessionSummary 是当前会话摘要。
Mem0 是跨会话长期语义记忆。
```

## 3. 回答前 search 怎么工作

当前项目在 `/chat/stream` 构建 `MemoryContext` 后调用长期记忆检索：

```text
用户问题
  ↓
LongTermMemoryService.search(user_id, query)
  ↓
返回相关长期记忆 top-k
  ↓
写入 MemoryContext.long_term_memories
  ↓
format_memory_context() 注入 Prompt
```

Mem0 的 search 不是全量注入长期记忆，而是按当前问题检索相关记忆：

```text
长期记忆库
  ↓ user_id 过滤
  ↓ query 语义检索
  ↓ top-k 限制
相关长期记忆
```

当前默认配置：

```python
mem0_search_limit = 5
```

也就是说，每次最多只注入 5 条相关长期记忆，避免 prompt 膨胀。

## 4. 回答后 add 为什么要门控

长期记忆不能每轮都写，否则会产生 memory pollution。

当前项目只允许以下内容写入 Mem0：

- 用户明确说“记住……”
- 用户表达长期目标，例如“我的目标是……”
- 用户表达稳定偏好，例如“我希望以后……”
- 用户补充项目背景或系统事实，例如“当前记忆系统是……”

不会写入：

- 普通 RAG 问答。
- 错误回答。
- 超时回答。
- “无法回答”类内容。
- 知识库无关回答。
- 工具 trace 或中间执行过程。

写入链路：

```text
回答完成
  ↓
保存 PostgreSQL 完整历史
  ↓
触发 SessionSummary 后台压缩
  ↓
如果通过长期记忆门控
  ↓
后台 LongTermMemoryService.add(user_id, messages)
```

这样 Mem0 写入不会阻塞用户回答，也不会污染长期记忆。

## 5. 当前项目的降级策略

Mem0 是增强能力，不是核心依赖。

当前配置默认关闭：

```python
mem0_enabled = False
mem0_mode = "platform"
mem0_api_key = ""
mem0_search_limit = 5
mem0_async_add = True
```

当 Mem0 没有开启、没有配置 key、SDK 不可用或调用失败时：

```text
search 返回 []
add 直接跳过
核心 RAG 流程继续执行
```

这保证了长期记忆故障不会影响知识库问答主链路。

## 6. 当前实现位置

配置项：

```text
app/config.py
```

长期记忆适配层：

```text
app/services/long_term_memory_service.py
```

会话记忆上下文：

```text
app/services/memory_service.py
```

Prompt 注入：

```text
app/agentic/node_synthesizer.py
```

主链路接入：

```text
app/api.py
```

## 7. 面试怎么讲

可以这样回答：

> 我的记忆系统不是单一 memory buffer，而是分层设计。PostgreSQL 保存完整历史，Redis 保存最近窗口，SessionSummary 压缩当前 session 的旧上下文，Mem0 负责跨 session 的长期语义记忆。Mem0 不是全量注入，而是按 user_id 和当前 query 检索 top-k 相关记忆；写入时也不是每轮都写，而是通过门控只保存用户目标、偏好、项目背景和稳定事实，避免长期记忆污染。Mem0 失败时会降级为空记忆，不影响核心 RAG 问答。

如果面试官继续问“为什么不用 Mem0 替代 Redis”，可以回答：

> Redis 和 Mem0 解决的问题不同。Redis 是当前会话的短期窗口，强调低延迟和最近上下文；Mem0 是跨会话长期语义记忆，强调用户偏好和长期事实。两者是分层协作，不是替代关系。

如果面试官问“长期记忆会不会占用很多上下文”，可以回答：

> 不会全量注入。每次只根据当前 query 在当前 user_id 范围内检索 top-k 相关记忆，并限制条数和长度。长期记忆只是用户背景和偏好，不替代知识库证据。
