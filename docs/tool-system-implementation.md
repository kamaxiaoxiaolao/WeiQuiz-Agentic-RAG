# WeiQuiz 工具系统实现与优化说明

## 当前架构

WeiQuiz 的工具系统采用 `Router -> Controller -> Tool Planner -> Tool Registry -> Tool Handler` 的链路：

1. `Router` 判断用户问题是否需要工具，例如 web search 或 SQL。
2. `AgentController` 将这类问题切到 `TOOL_CALL` 模式。
3. `Tool Planner` 使用 function calling 选择工具并生成参数。
4. `ToolRegistry` 做权限检查、参数校验、默认值填充、同步/异步执行和结果标准化。
5. API 将 `ToolCallResult` 写入 trace 和会话历史。

核心文件：

| 文件 | 职责 |
| --- | --- |
| `app/tools/models.py` | 工具协议模型：`ToolSpec`、参数 schema、调用结果 |
| `app/tools/registry.py` | 工具注册、权限、参数校验、执行和结果标准化 |
| `app/tools/planner.py` | 使用 function calling 规划工具调用，失败时做规则 fallback |
| `app/tools/web_search.py` | Web Search 多 provider 适配器 |
| `app/tools/mcp_client.py` | MCP 工具发现和远程调用客户端 |
| `app/api.py` | TOOL_CALL 模式下执行工具并返回 SSE |

## 本轮优化

### 1. 异步工具执行链路

`web_search` 现在按 async handler 注册，API 使用 `await registry.call_async(...)` 执行工具，避免在 FastAPI 事件循环里嵌套 `asyncio.run()`。

### 2. 参数校验

`ToolRegistry` 会在 handler 执行前根据 `ToolSpec.input_schema` 做：

- 必填参数检查
- 默认值填充
- 类型转换，例如 `"3"` 转成 `3`
- enum 校验
- 丢弃未声明参数

这样工具 handler 不再直接面对模型随意生成的原始参数。

### 3. Planner fallback

当 Tool Planner LLM 不可用、没有返回 tool call 或参数 JSON 错误时，如果当前只允许一个工具，系统会构造确定性参数：

| 工具 | fallback 参数 |
| --- | --- |
| `web_search` | `{"query": 用户问题}` |
| `kb_search` | `{"query": 用户问题}` |
| `memory_search` | `{"query": 用户问题}` |
| `sql_query` | `{"question": 用户问题}` |

这让明确意图的问题不会因为 planner 模型失败而整条链路中断。

### 4. Web Search mock 收口

MCP web search 失败时不再默认返回假搜索结果。只有显式设置 `WEB_SEARCH_MOCK_ENABLED=true` 时，才返回 mock 数据。

### 5. 长期记忆工具接入

`memory_search` 已从占位工具接入 `LongTermMemoryService.search()`。Mem0 未启用时会稳定返回空结果，启用后可作为用户级长期记忆检索工具。

## 当前可用状态

| 工具 | 状态 | 说明 |
| --- | --- | --- |
| `web_search` | 半可用 | 有 provider 适配；需要配置真实 MCP/API；mock 需显式开启 |
| `memory_search` | 可用 | 已接长期记忆服务；依赖 Mem0 配置决定是否有结果 |
| `kb_search` | 占位 | 需要接现有 RAG retriever/workflow |
| `sql_query` | 占位 | 需要安全 SQL 沙箱、白名单和审计 |

## 后续优化方向

1. 接入真实 `kb_search`：复用现有 retriever，返回结构化 source nodes。
2. 接入安全 `sql_query`：只读连接、表白名单、SQL AST 校验、强制 limit、审计日志。
3. 工具结果综合生成：不要直接把工具 raw content 返回用户，而是交给 LLM 做摘要、引用和解释。
4. 工具可观测性：记录 provider、参数、耗时、结果数量、失败原因。
5. 多工具计划：支持先 web search 再 RAG 或 memory search，再统一综合回答。

一句话总结：

> 当前工具系统已经从“工具声明 + 占位执行”优化为“异步执行、参数校验、planner fallback、显式 mock、安全结果标准化”的基础工具框架，下一步重点是接真实业务工具和做工具结果综合生成。
