# AgentController 设计说明

## 1. 定位

WeiQuiz 的主体仍然是 **RAG 系统**，AgentController 是 RAG 上方的 **决策调度层**。

它不负责真正检索、不执行工具、不写入记忆、不生成最终答案，而是负责判断：

- 这个问题要不要走知识库检索。
- 要不要先让用户补充信息。
- 要不要直接闲聊回答。
- 要不要调用外部工具。
- RAG Workflow 应该使用什么策略。
- 是否需要答案生成后的 Grounding / Reflection。
- 记忆上下文应该取多少。
- 最大重试次数是多少。

一句话：**AgentController 是大脑，RAG Workflow 是执行链路，Tool Registry 是工具执行层，MemoryService 是记忆执行层。**

## 2. 当前请求链路

当前 `/chat/stream` 的主流程是：

```text
用户输入
  -> FastAPI /chat/stream
  -> AgentController.decide()
  -> AgentDecision
  -> 按 mode 分支执行
      -> clarification: 反问用户补充信息
      -> chitchat: 直接生成闲聊回答
      -> tool_call: 使用工具规划结果调用 Tool Registry
      -> rag_workflow: 进入 Agentic RAG Workflow
  -> SSE 流式返回
  -> 保存会话历史
  -> 触发记忆压缩检查
```

这使得入口层不再直接堆满路由、工具、记忆、RAG 策略判断，而是先统一得到一个结构化决策。

## 3. 核心数据结构

### AgentMode

当前有四种执行模式：

- `chitchat`：闲聊或不需要检索的问题，直接生成回答。
- `clarification`：问题信息不足，先反问用户。
- `tool_call`：需要外部工具，例如 Web Search、SQL、Memory Search。
- `rag_workflow`：需要知识库检索，进入 RAG Workflow。

### MemoryPolicy

用于告诉下游是否需要注入记忆，以及长期记忆召回数量：

- 简单闲聊可以取更多长期记忆，增强连续性。
- 工具调用通常少取，避免干扰工具参数。
- RAG 问答取适量记忆，主要用于指代补全和用户偏好。

### ClarificationDecision

用于描述是否需要澄清：

- `needed`：是否需要反问。
- `question`：要问用户的问题。
- `reason`：为什么需要澄清。
- `missing_slots`：缺失的信息槽位。
- `method`：规则触发还是 LLM 判断。

### AgentDecision

Controller 的最终输出，包含：

- `mode`
- `route`
- `reason`
- `memory_policy`
- `clarification`
- `tool_plan`
- `rag_strategy`
- `need_grounding`
- `max_retries`

这些字段会进入 `controller_decision`，并写入 SSE 过程事件和最终 trace，方便前端展示与后端排查。

## 4. 当前决策流程

AgentController 当前按下面顺序工作：

```text
1. route_query(query)
   判断基础意图，例如 knowledge_base / web_search / sql_query / memory / chitchat。

2. decide_clarification(query, route)
   先做规则判断，再用 LLM fallback 判断是否需要澄清。

3. decide_mode(route, clarification)
   如果需要澄清，优先进入 clarification。
   否则根据 route 映射到 chitchat / tool_call / rag_workflow。

4. decide_memory_policy(mode)
   决定是否注入记忆，以及长期记忆 top_k。

5. plan_tool_call(query, route)
   仅当 mode = tool_call 时触发。
   使用 Function Calling 方式生成工具名和参数。

6. decide_rag_strategy(route)
   决定 RAG Workflow 的策略入口。

7. decide_grounding(mode, route)
   判断是否建议生成后做 Grounding / Reflection。

8. 输出 AgentDecision
```

## 5. 澄清机制

澄清分支解决的是一个很实际的问题：**问题本身信息不足时，不应该强行检索或强行编造答案。**

当前采用 **规则优先 + LLM fallback**：

### 规则优先

对高确定性模糊问题直接触发，例如：

- “对比这两个”
- “哪个更好”
- “总结这个”
- “分析这个问题”
- “帮我处理一下”

这类问题缺少明确对象、范围或约束，直接检索容易召回噪声。

### LLM fallback

规则没命中时，再让 LLM 判断：

- 信息是否足够。
- 是否需要反问。
- 缺少哪些字段。
- 应该问用户什么。

这样做的原因是：规则便宜稳定，LLM 更灵活。两者结合可以减少无意义的 LLM 调用，同时覆盖复杂模糊表达。

### 面试表达

可以这样说：

> 我们在 AgentController 中实现了前置澄清机制。它不是等检索失败后再补救，而是在进入 RAG 前先判断用户问题是否具备可执行条件。对于明显缺少对象或范围的问题，规则直接触发反问；对于边界模糊的问题，再交给 LLM 做结构化判断。这样可以减少错误检索、降低幻觉风险，也能节省后续检索和生成 token。

## 6. 工具调用层关系

当前工具链分三层：

```text
AgentController
  -> 判断是否需要工具
  -> 调用 Tool Planner 生成工具调用计划

Tool Planner
  -> 使用 Function Calling
  -> 输出 tool_name + arguments

Tool Registry
  -> 校验工具是否存在
  -> 执行具体工具 Adapter
  -> 返回 ToolCallResult
```

Controller 不直接执行工具。这样做是为了让职责清晰：

- Controller 负责“该不该用工具”。
- Tool Planner 负责“用哪个工具、参数是什么”。
- Registry 负责“工具注册、权限、执行与错误封装”。
- Adapter 负责“对接具体外部系统”。

当前已预留的工具包括：

- `kb_search`
- `web_search`
- `sql_query`
- `memory_search`

其中 Web Search 默认未配置真实联网能力，当前会返回 `tool_not_configured`，后续可接 MCP Server 或搜索 API。

## 7. RAG Workflow 边界

AgentController 不做 RAG 的细节，只决定是否进入 RAG Workflow，以及给出策略建议。

RAG Workflow 层负责：

- Query Planning
- 子问题拆解
- HyDE
- Step-back
- Hybrid Retrieval
- Rerank
- Quality Check
- Rewrite / Retry
- Intermediate Synthesis
- Grounding / Reflection

也就是说：

```text
AgentController 决定“走不走 RAG、用什么策略、是否反思”
RAG Workflow 负责“怎么检索、怎么重试、怎么综合、怎么校验”
```

这符合 Agentic RAG 的主流分层：上层 Agent 做决策调度，下层 RAG Workflow 做检索增强执行。

## 8. 记忆策略边界

当前记忆系统有三层：

- PostgreSQL：完整会话历史。
- Redis / ChatMemoryBuffer：最近窗口缓存。
- SessionSummary：滚动摘要压缩。

AgentController 只输出 `MemoryPolicy`，不直接读取或写入记忆。

真正执行记忆读取的是 MemoryService：

- 根据 session_id 读取最近消息窗口。
- 根据 SessionSummary 注入旧上下文摘要。
- 根据长期记忆策略召回 Mem0 语义记忆。

这样做的好处是：Controller 只管策略，MemoryService 只管实现，后续替换 Mem0、Zep、LangMem 时不会影响 Controller 主流程。

## 9. Grounding 与 Retry 策略

当前重试策略默认收敛为：

- `max_retries = 1`

原因是 Agentic RAG 如果无限重写和重试，会显著增加延迟和 token 成本。企业级系统通常会设置明确的最大轮数，并把失败原因写入 trace。

Grounding 当前支持三种模式：

- `off`：关闭反思校验。
- `auto`：由 AgentController 决策是否需要。
- `reflection`：强制开启反思模式。

推荐默认使用 `auto`，前端可以提供模式选择：

- 普通模式：更快。
- 反思模式：更可信但更慢。

## 10. 可观测性

Controller 的决策会写入：

- SSE 路由事件。
- RAG trace。
- 最终响应结果。

核心字段是 `controller_decision`。

这样排查问题时可以直接看到：

- 为什么走了 RAG。
- 为什么没有走 Web Search。
- 为什么触发澄清。
- 当前使用了什么 memory policy。
- 是否开启 Grounding。
- 最大重试次数是多少。

## 11. 当前实现是否已经是 Agentic RAG

可以称为 **基础到中级 Agentic RAG**。

原因是当前已经具备：

- 决策层：AgentController。
- 路由层：intent routing。
- 策略层：RAG strategy。
- 工具层：Tool Planner + Tool Registry。
- 记忆层：短期窗口 + 滚动摘要 + 长期记忆。
- 检索层：Hybrid Retrieval + Rerank。
- 质量层：Quality Check + Rewrite / Retry。
- 反思层：Grounding / Reflection。
- 可观测层：trace + controller_decision。

但它还不是完全成熟的企业级 Agentic RAG，因为后续还可以继续加强：

- 真正的 MCP Web Search 工具。
- 更完整的 Plan-and-Execute。
- 更强的多工具编排。
- GraphRAG。
- 更系统的 RAGAS / 自建评测闭环。
- 文档级权限与审计。

## 12. 面试回答模板

如果面试官问：“你们的 AgentController 是怎么设计的？”

可以回答：

> 我们的系统本质还是 RAG，但在 RAG 前面加了一层 AgentController 作为决策大脑。它不直接做检索和生成，而是统一判断用户问题应该走闲聊、澄清、工具调用还是知识库 RAG。Controller 会结合路由结果、澄清判断、记忆策略、工具规划、RAG 策略和 Grounding 策略，输出结构化的 AgentDecision。下游再根据这个决策进入不同执行链路。
>
> 这样做的好处是把“决策”和“执行”拆开：Controller 负责判断和调度，RAG Workflow 负责检索增强，Tool Registry 负责工具执行，MemoryService 负责记忆读取和压缩。后续扩展 Web Search、SQL、Mem0 或 GraphRAG 时，不需要把所有逻辑塞到接口层里。

## 13. 下一步优化方向

当前最适合继续推进的是：

1. 将 Web Search 接入 MCP 或真实搜索 API。
2. 把工具调用结果与 RAG Workflow 做统一上下文融合。
3. 增强 Controller 的 Plan-and-Execute 能力，但不要把它做成通用 Agent，仍然围绕 RAG 场景。
4. 增加文档级权限过滤，避免用户检索到无权限文档。
5. 完善 trace 前端展示，让每一步决策都能被看见。
