# WeiQuiz Agentic RAG 差距分析与优先级路线图

更新时间：2026-05-20

## 1. 结论摘要

当前 WeiQuiz 已经不是一个简单的 RAG Demo，而是具备 Agentic RAG 雏形的企业知识库系统：

- 已将 Query Router 升级为 Unified Query Analyzer 第一版，可以一次输出意图、复杂度、query strategy、候选工具和 grounding 建议。
- 已有 Hybrid Retrieval，包含向量检索、BM25、RRF 融合和 rerank。
- 已有基础 Agentic Workflow，包含 Router、Retrieval、Quality Check、Query Rewrite、Generation。
- 已新增 Sub-question Decomposition + Multi-hop Retrieval，复杂问题会拆成多个子问题分别检索并合并证据。
- 已有 SSE 流式输出和前端 Agentic RAG Trace 展示。
- 已有基础账号、权限、会话、Redis 记忆和检索评估准备。

但从企业级主流 Agentic RAG 来看，当前系统仍处于“可演示的 Agentic RAG 第一阶段”，核心差距不在于有没有组件，而在于这些组件是否形成稳定、可评估、可解释、可迭代的闭环。

最关键的差距是：

1. 当前多跳检索更接近 Multi-query Retrieval，还不是真正的 Iterative Multi-hop Reasoning。
2. 当前缺少子答案综合与证据归因，多个子问题的证据只是合并后交给最终生成。
3. 当前缺少 Faithfulness / Grounding 检查，不能自动判断回答是否被证据支撑。
4. 当前评估主要偏检索，还没有端到端 RAG 质量评估与回归测试。
5. 当前工具路由存在接口形态，但 Web Search / SQL Tool 等真实工具链还没有闭环。
6. 当前权限体系已有 user/admin，但缺少企业知识库常见的文档级 ACL 与检索过滤。

## 2. 阶段差距总览表

| 阶段 | 当前项目实现 | 主流 Agentic RAG 实现 | 核心差距 | 优先级 | 下一步 |
|---|---|---|---|---|---|
| 数据接入与预处理 | 已支持文档扫描、SHA256 增量 diff、PDF / Markdown / Text 解析、文本清洗、chunk 切分、Milvus 入库 | 结构化解析 PDF / Word / HTML / 表格 / 图片 / 代码块，统一 Doc / Block / Chunk metadata，生成 ingestion report 和 audit log | 当前解析和 metadata 还不够标准化，失败记录、入库报告、可追踪链路还不完整 | P1 | 完善 metadata schema、ingestion report、失败文档记录 |
| 索引与存储 | 已有 Milvus 向量库、PostgreSQL、Redis，支持 Dense / BM25 / Hybrid 基础索引 | 多索引体系：Vector Index、Sparse Index、DocStore、Metadata Store、Graph Index、权限索引 | 缺少 Graph Index、权限索引和面向评估/引用的统一 metadata store | P2 | 先补文档级 ACL metadata，再考虑 GraphRAG |
| 查询理解 | 已有 Unified Query Analyzer 第一版、Query Rewrite、Sub-question Decomposition，并已接入 HyDE 与 Step-back 第一版执行链路 | Router + Rewrite + HyDE + Step-back + Query Planning + Ambiguity Detection | 仍缺歧义澄清、策略消融和 memory-aware query normalization | P1 | 先做策略评估和歧义引导，再做多轮上下文补全 |
| 检索执行 | 已有 Dense Retrieval、BM25、RRF、Rerank、Multi-hop Retrieval 基础版 | Hybrid Retrieval、Parent Retrieval、Contextual Retrieval、Dynamic top_k、Metadata Filter、Evidence-aware Multi-hop | 当前多跳更像 multi-query retrieval，缺少基于上一跳证据动态生成下一跳查询 | P1 | 做 evidence-aware multi-hop |
| Agentic Workflow | 已有 Workflow-first 流程：Router -> Retrieval -> Quality Check -> Rewrite -> Generation，并新增 decomposition 分支 | Planner / Executor / Verifier 分层，状态机管理 task state，支持受控循环和工具选择 | 当前流程仍主要由代码固定编排，LLM 还不是核心 planner | P1 | 抽象 PlanStep，拆出 Planner / Executor / Verifier |
| 子问题分解与多跳 | 已能把 multi_step 问题拆成多个子问题，分别检索并去重合并证据 | Decompose-and-synthesize：子问题 -> 检索 -> 中间答案 -> 综合答案，每个子答案绑定证据 | 当前缺少 intermediate answer，证据没有按子问题归因 | P0 | 做子答案综合和证据归因 |
| 证据综合与生成 | 已能基于召回上下文生成回答，展示来源和 Trace | Evidence Merge、Sub-answer Synthesis、Citation、Conflict Resolution、Refusal | 缺少冲突证据处理、逐句引用、证据不足拒答策略 | P0 | 先实现子答案综合，再做引用和拒答 |
| Reflection / Grounding | 已有 Retrieval Quality Check，低质量时触发 Query Rewrite | Retrieval Reflection、Answer Reflection、Claim-level Grounding、Self-correction | 当前只有检索前/检索后的基础质量判断，没有答案生成后的忠实性检查 | P0 | 加入 faithfulness / grounding check |
| GraphRAG | 暂未实现 | Entity Graph、Relation Extraction、Community Summary、Path Retrieval、Graph + Vector 融合 | 当前知识组织仍是 chunk-based，缺少实体关系层 | P3 | 等基础评估和 grounding 完成后再做 |
| 工具调用 | Router 中已有 Web Search / SQL Query 意图，但真实工具闭环不足 | Tool Registry、Function Calling、MCP Server、Web / SQL / API Tool、工具结果注入证据链 | 当前工具更多是意图占位，不是完整 tool execution | P2 | 先实现一个真实工具闭环，如 Web Search 或 SQL Assistant |
| 记忆系统 | 已有 Redis 会话缓存和短期会话上下文 | 短期窗口记忆、摘要记忆、长期语义记忆、用户偏好、Mem0 / Zep / LangMem 类记忆抽取更新 | 缺少跨会话长期记忆、记忆抽取、记忆更新与遗忘机制 | P3 | 当前先不优先，等主链路稳定后再补长期记忆 |
| 权限与安全 | 已有注册登录、JWT、user/admin RBAC，会话按用户隔离 | 文档级 ACL、Chunk 级权限继承、检索阶段权限过滤、审计日志、Guardrails、Prompt Injection 防护 | 缺少文档级权限过滤和输入输出安全防护 | P2 | 补文档 ACL metadata + 检索 filter |
| 评估体系 | 已有检索评估准备，已新增 RAGAS 最小评估入口 | Retrieval Eval + RAGAS / DeepEval / LlamaIndex Eval + 消融实验 + 回归测试 | 还没有跑出稳定 baseline，也没有形成优化前后对比报告 | P0 | 先跑 RAGAS baseline，再做后续优化 |
| 可观测性 | 前端已展示 Agentic RAG Trace，后端记录部分 timings | Langfuse / LangSmith / Phoenix，记录 query、retrieval、rerank、prompt、answer、token、latency、feedback | 当前 Trace 主要用于前端展示，缺少持久化和线上分析 | P2 | 接入 Langfuse 或先落库保存 WorkflowTrace |
| 工程稳定性 | 已有 FastAPI、SSE、Vue 前端、基础权限和缓存 | 会话锁、队列、限流、异常兜底、快速返回、配置热更新、CI/CD | 缺少队列、会话锁、限流和系统级 fallback | P2 | 补会话锁、限流和异常兜底 |

从优先级看，当前最应该先做的不是继续堆新功能，而是：

```text
RAGAS baseline
  -> 子答案综合
  -> Grounding / Answer Reflection
  -> Evidence-aware Multi-hop
  -> 工具调用与权限过滤
  -> GraphRAG / 长期记忆
```

## 3. 企业级大功能开发优先级

后续开发策略调整为“大功能闭环优先，小优化后置”。每个阶段都要尽量做到企业级主流的最小闭环，而不是只实现一个 demo 级接口。

| 开发阶段 | 大功能闭环 | 企业级主流实现标准 | 当前状态 | 完成标准 | 优先级 |
|---|---|---|---|---|---|
| Phase A | 评估闭环 | 固定评估集、RAGAS 指标、检索指标、报告落盘、优化前后可对比 | 已新增 RAGAS 脚本，已有 3 条样本 baseline，仍需稳定 10-50 条 | 能稳定输出 `ragas_report.md` 和 `ragas_scores.json`，记录 faithfulness、context_precision、context_recall 等指标 | P0 |
| Phase B | 可信生成闭环 | 子问题 -> 证据 -> 中间答案 -> 最终综合；生成后 Grounding / Faithfulness 检查；证据不足拒答或重试 | 已有子问题分解和多跳检索，但缺少子答案综合和答案级验证 | 每个子问题有 intermediate answer 和 sources，最终答案经过 grounding check | P0 |
| Phase C | 检索增强闭环 | 原始 Query、Query Rewrite、HyDE、Step-back、BM25、Dense、Hybrid 多策略召回，RRF/加权融合，Rerank，评估对比 | 已有 Dense + BM25 + RRF + Rerank，并已接入 Analyzer 自动触发的 HyDE 与 Step-back 第一版；仍缺策略消融与更细粒度融合 | 关键查询策略可进入 trace，后续能和 baseline 对比 Recall / Precision / RAGAS 变化 | P1 |
| Phase D | 工具增强闭环 | Tool Registry / MCP Server / Web Search / SQL Tool；工具结果转为统一 evidence；工具调用进入 trace 和 grounding | Router 有 Web Search / SQL Query 意图，但缺少真实工具执行 | 本地 KB 证据不足时可触发 Web Search，并和 KB evidence 统一融合 | P1 |
| Phase E | Agentic 架构升级 | Planner / Executor / Verifier 分层，受控 Agent Loop，max_steps、状态管理、失败恢复 | 当前是 Workflow-first，LLM 只参与关键节点 | 复杂问题进入受控 loop，简单问题仍走稳定 workflow | P2 |
| Phase F | 企业安全与工程化 | 文档级 ACL、检索权限过滤、Prompt Injection 防护、限流、会话锁、异常兜底、Trace 持久化 | 已有 JWT / RBAC / SSE / Trace，但缺少文档级权限和工程保护 | 用户只能检索有权限文档，异常可降级，trace 可追踪 | P2 |
| Phase G | 高级知识组织 | GraphRAG、长期记忆、Multi-agent 专家工具分工 | 暂未实现 | 在主链路稳定、评估可靠后引入，作为高级增强能力 | P3 |

推荐开发主线：

```text
Phase A：RAGAS / 评估闭环
  -> Phase B：子答案综合 + Grounding
  -> Phase C：HyDE + 多策略检索增强
  -> Phase D：MCP Web Search 工具增强
  -> Phase E：Planner / Executor / Verifier 架构升级
  -> Phase F：权限、安全、工程化
  -> Phase G：GraphRAG / 长期记忆 / Multi-agent
```

这个顺序的核心原则是：

- 先有评估，否则无法证明后续优化有效。
- 先保证可信生成，否则召回再多也可能产生幻觉。
- 再增强检索，因为 HyDE、GraphRAG、Web Search 都需要评估和 grounding 支撑。
- 工具调用要在证据格式统一和可信控制之后做，否则容易引入外部噪声。
- GraphRAG、长期记忆属于高级增强，不应早于评估闭环和可信生成闭环。

## 4. 当前系统能力基线

### 4.1 数据与索引

当前能力：

- 支持文档扫描、SHA256 增量 diff。
- 支持 PDF / Text / Markdown 解析。
- 支持基础文本清洗、标题修正、页眉页脚去重。
- 已有 Block 抽象、metadata 保留、chunk 切分。
- 支持 Milvus 入库。

与主流差距：

- 文档结构解析还不够精细，表格、图片、代码块、标题层级、页码区域等没有完全标准化。
- chunk metadata 还没有成为检索、权限、评估、引用的统一基础设施。
- ingestion report、失败文档记录、可追踪 audit 链路仍需强化。

### 4.2 检索

当前能力：

- Dense Vector Retrieval。
- BM25 Sparse Retrieval。
- RRF Hybrid Fusion。
- Rerank。
- 基础检索评估脚本与 SQuAD 数据集准备。

与主流差距：

- top_k、candidate_k、rerank_top_n 等参数还没有通过实验系统调优。
- Dense / Sparse / Hybrid / Hybrid + Rerank 的消融评估还没有形成固定报告。
- 还没有多粒度索引、上下文增强 chunk、metadata filter、query-aware retrieval strategy。

### 4.3 Agentic Workflow

当前能力：

- Unified Query Analyzer 判断问题意图、复杂度、query strategy 和候选工具。
- Retrieval 执行知识库检索。
- Quality Check 判断检索质量。
- Query Rewrite 在低质量检索时触发重试。
- query_strategy 为 decomposition 的问题会进入子问题分解和多路检索。
- 前端可以展示 Agentic RAG Trace。

与主流差距：

- Workflow 仍以固定流程为主，不是完全动态决策。
- 多跳检索目前是“先拆问题，再并行/顺序分别检索”，还没有“第一跳结果影响第二跳问题”的闭环。
- 没有 Self-Reflection，也没有答案生成后的自检。
- 没有明确的 planner / executor / verifier 分层。

### 4.4 生成与可信控制

当前能力：

- 能基于召回证据生成答案。
- 可以在前端展示来源和 Trace。

与主流差距：

- 缺少子答案综合。
- 缺少逐句引用和证据对齐。
- 缺少回答前后的 Grounding 检查。
- 缺少冲突证据处理。
- 缺少“证据不足时拒答”的强约束策略。

### 4.5 记忆系统

当前能力：

- Redis 会话记忆。
- 基础短期会话历史管理。
- 已讨论过 MemoryService / ChatMemoryBuffer 方向。

与主流差距：

- 还没有真正跨会话长期记忆。
- 没有用户画像、偏好记忆、事实记忆、任务记忆的分层。
- 没有记忆抽取、记忆压缩、记忆更新、记忆遗忘的完整机制。
- 没有 Mem0 / Zep / LangMem 类系统的长期记忆闭环。

### 4.6 安全与权限

当前能力：

- JWT 登录注册。
- user/admin RBAC。
- 文档管理接口限制 admin。
- 普通用户只能访问自己的会话。

与主流差距：

- 缺少文档级 ACL。
- 缺少 chunk 级权限继承。
- 缺少检索阶段 metadata permission filter。
- 缺少审计日志。
- 缺少 Prompt Injection 防护、输入输出 Guardrails、敏感信息脱敏。

### 4.7 评估与可观测性

当前能力：

- 已开始做检索评估。
- 已准备 SQuAD 检索评估数据。
- 前端可以展示 RAG Trace。

与主流差距：

- 缺少固定评估集和指标基线。
- 缺少端到端 RAGAS / DeepEval / LlamaIndex Evaluation。
- 缺少线上 trace 平台，例如 Langfuse、LangSmith、Phoenix。
- 缺少每次改动后的回归测试。
- 缺少成本、延迟、召回、精排、生成质量的统一 dashboard。

## 5. 优先级路线图

### P0：可信答案闭环

P0 是最优先要补的部分，因为它直接决定这个项目能不能从“能回答”升级成“回答可信”。

#### P0-1：子答案综合

当前状态：

- 已经可以将复杂问题拆成多个子问题。
- 每个子问题会单独检索。
- 检索结果会合并去重后交给最终生成。

差距：

- 系统没有先回答每个子问题。
- 最终生成阶段不知道每条证据对应哪个子问题。
- 多跳链路在语义上还不够清晰。

主流做法：

```text
复杂问题
  -> 子问题 1 -> 检索证据 -> 中间答案 1
  -> 子问题 2 -> 检索证据 -> 中间答案 2
  -> 子问题 3 -> 检索证据 -> 中间答案 3
  -> 综合中间答案 -> 最终答案
```

下一步实现：

- 为每个 sub_question 保存对应 source_nodes。
- 新增 intermediate_answers。
- 最终 prompt 中显式注入“子问题、证据、中间答案”。
- 前端 Trace 展示每个子问题的中间答案。

面试价值：

> 我的系统不是简单地把复杂问题改写一下，而是做 decompose-and-synthesize。先把复杂问题拆成多个可检索子问题，每个子问题独立召回证据并形成中间答案，最后综合多个中间答案生成最终回答。

#### P0-2：Grounding / Faithfulness 检查

当前状态：

- 有检索质量检查。
- 没有答案生成后的事实一致性检查。

差距：

- 无法判断最终答案是否被证据支持。
- 无法自动发现幻觉。
- 无法要求模型在证据不足时拒答。

主流做法：

- 生成后执行 Faithfulness Judge。
- 将 answer 拆成 claim。
- 每个 claim 回查 context 是否支持。
- 输出 supported / unsupported / partially supported。

下一步实现：

- 新增 answer_grounding_check。
- 对最终回答做 claim-level 判断。
- 如果 unsupported 比例过高，触发重写回答或拒答。
- Trace 中展示 grounding_score。

面试价值：

> 企业 RAG 不能只追求回答流畅，还要保证回答可验证。我会在生成后增加 grounding check，把答案拆成事实声明，再判断每个声明是否能被召回证据支持。

#### P0-3：检索评估闭环

当前状态：

- 已准备 SQuAD 检索评估数据。
- 可以先做 Hit@K、Recall@K、MRR。

差距：

- 还没有形成固定 benchmark。
- 没有对 Dense / BM25 / Hybrid / Rerank 做消融对比。
- 没有把评估结果固化到文档或报告。

主流做法：

- 固定评估集。
- 固定指标。
- 每次检索策略变化都跑回归。
- 用实验报告证明优化有效。

下一步实现：

- 跑 Dense only、BM25 only、Hybrid、Hybrid + Rerank 四组实验。
- 输出 markdown / json 报告。
- 记录 Hit@5、Recall@5、MRR@5、Latency。

面试价值：

> 检索优化不能只凭感觉。我会用公开数据集先建立可重复 benchmark，再对 Dense、Sparse、Hybrid、Rerank 做消融实验，证明每个组件对召回率和排序质量的贡献。

### P1：真正 Agentic 化

P1 是让系统从“固定 RAG 工作流”升级到“能根据问题动态决策”的关键。

#### P1-1：Evidence-aware Multi-hop

当前状态：

- 子问题之间基本独立。
- 第一跳结果不会影响第二跳。

差距：

- 这更像 Multi-query Retrieval，不是真正的 Multi-hop Reasoning。

主流做法：

```text
第一跳检索
  -> 阅读证据
  -> 发现缺失信息
  -> 生成下一跳问题
  -> 第二跳检索
  -> 直到证据足够或达到最大步数
```

下一步实现：

- 每一跳后判断是否证据足够。
- 根据已有证据动态生成 next_query。
- 限制最大 hop 数，避免死循环。
- Trace 记录每跳 query、evidence、decision。

#### P1-2：Planner / Executor / Verifier 分层

当前状态：

- Workflow 中混合了承担路由、检索、质量判断、改写的逻辑。

差距：

- 职责边界还不够清晰。
- 后续扩展工具会越来越乱。

主流做法：

- Planner：决定任务步骤。
- Executor：调用检索、搜索、SQL、工具。
- Verifier：判断结果是否可信、是否需要重试。

下一步实现：

- 抽象 PlanStep。
- Router 只负责意图，不负责执行细节。
- Workflow 根据 PlanStep 调用不同 executor。
- Verifier 统一处理质量判断、grounding、retry。

#### P1-3：真实工具调用闭环

当前状态：

- Router 有 Web Search / SQL Query 意图。
- 但还没有真实工具实现闭环。

差距：

- 当前工具路由更像占位符。
- 企业 Agentic RAG 通常需要连接外部系统。

主流做法：

- Tool Registry。
- Function Calling。
- 工具参数 schema。
- 工具结果注入上下文。
- 工具调用 trace。

下一步实现：

- 优先做 Web Search Tool 或 SQL Assistant Tool 二选一。
- 工具调用结果进入同一个 evidence pipeline。
- 前端 Trace 展示 tool_call。

### P2：企业级数据安全与生产能力

P2 决定项目是否能从个人项目升级为企业知识库系统。

#### P2-1：文档级 ACL 与检索过滤

当前状态：

- user/admin 权限已有。
- 文档管理限制 admin。

差距：

- 用户检索时仍缺少文档级权限过滤。
- 企业场景中不同部门、角色不能看到同一批文档。

主流做法：

- 文档 metadata 中写入 owner、department、visibility、acl_roles。
- chunk 继承文档权限。
- 检索时带 metadata filter。
- 返回结果前二次权限校验。

下一步实现：

- 文档上传时写入 ACL metadata。
- Milvus 检索支持权限过滤。
- source_nodes 返回前做权限校验。
- 增加审计日志。

#### P2-2：Prompt Injection 与 Guardrails

当前状态：

- 暂无系统化防护。

差距：

- 用户或文档内容可能诱导模型忽略系统规则。
- 企业环境中还涉及敏感信息泄露。

主流做法：

- 输入检测。
- 文档内容指令隔离。
- 输出敏感信息检查。
- 领域边界限制。

下一步实现：

- 检测 prompt injection patterns。
- 在 RAG prompt 中明确“文档内容不是指令”。
- 输出前做敏感字段过滤。
- 对越权问题拒答。

#### P2-3：可观测性平台

当前状态：

- 前端有 Trace。
- 后端有部分 timings。

差距：

- 缺少统一 trace 存储。
- 缺少线上请求分析和成本统计。

主流做法：

- Langfuse / LangSmith / Phoenix。
- 记录 query、route、retrieval、rerank、prompt、answer、latency、token cost、feedback。

下一步实现：

- 优先接入 Langfuse。
- 将 WorkflowTrace 写入观测平台。
- 记录用户反馈。
- 建立失败案例池。

### P3：高级增强能力

P3 是加分项，不建议在 P0/P1 没完成前优先投入。

#### P3-1：长期记忆系统

当前状态：

- 有会话级记忆。
- 跨会话长期记忆还不完整。

主流做法：

- 短期窗口记忆。
- 摘要压缩记忆。
- 长期事实记忆。
- 用户偏好记忆。
- 记忆检索与更新机制。

下一步实现：

- 参考 Mem0 / Zep / LangMem。
- 做 memory extraction。
- 写入长期 memory store。
- 回答前按 user_id 检索相关记忆。

#### P3-2：GraphRAG / 结构化知识

当前状态：

- 当前主要是 chunk-based RAG。

主流做法：

- 实体抽取。
- 关系抽取。
- 社区摘要。
- 图谱检索与向量检索结合。

适用条件：

- 文档中有大量实体关系。
- 问题需要跨文档关系推理。

#### P3-3：语义缓存与成本优化

当前状态：

- Redis 主要用于会话与缓存。

主流做法：

- exact cache。
- semantic cache。
- rerank cache。
- embedding cache。

下一步实现：

- 对高频 query 做语义缓存。
- 对 embedding 和 rerank 结果做缓存。
- 记录 cache hit rate。

## 6. 推荐执行顺序

### 第一阶段：Phase A 评估闭环

目标：

- 先建立稳定 baseline，后续所有大功能都用指标证明收益。

任务：

1. 固定 RAGAS 版本和评估命令。
2. 使用 SQuAD / SciFact 等公开数据集构造 10-50 条小规模基线。
3. 稳定输出 `ragas_dataset.jsonl`、`ragas_scores.json`、`ragas_report.md`。
4. 记录 `faithfulness`、`context_precision`、`context_recall`，后续再补 `answer_relevancy`。
5. 将 baseline 指标写入评估计划或项目日志。

完成标准：

- 可以一条命令复现当前系统的端到端 RAGAS 分数。
- 后续任何检索或生成优化都能和 baseline 对比。

### 第二阶段：Phase B 可信生成闭环

目标：

- 从“检索到证据后直接生成”升级为“证据归因、子答案综合、生成后校验”。

任务：

1. 将 `sub_question` 与对应 `source_nodes` 绑定。
2. 为每个子问题生成 `intermediate_answer`。
3. 最终答案基于多个 `intermediate_answers` 综合生成。
4. 增加 answer grounding / faithfulness check。
5. 证据不足时触发拒答、改写或二次检索。

完成标准：

- 前端 Trace 能看到子问题、证据、中间答案和最终综合答案。
- RAGAS faithfulness 不下降，context recall 尽量提升。

### 第三阶段：Phase C 检索增强闭环

目标：

- 提升复杂语义问题和表达不一致问题的召回能力。

任务：

1. 引入 HyDE，生成 hypothetical answer / document。
2. 并行执行原始 query、rewrite query、HyDE query 检索。
3. 将 Dense、BM25、Hybrid、HyDE 结果融合。
4. 统一进入 rerank。
5. 使用 RAGAS 和检索指标对比优化前后效果。

完成标准：

- HyDE 可配置开关。
- 有 HyDE 前后对比报告，而不是只说明“理论上提升”。

### 第四阶段：Phase D 工具增强闭环

目标：

- 当本地知识库证据不足时，引入外部补充召回通道。

任务：

1. 通过 MCP 接入 Web Search 工具。
2. 在检索质量不足或 Router 判断为 web_search 时触发工具。
3. 将 Web Search 结果清洗为统一 evidence 格式。
4. 与 KB evidence 融合、去重、排序。
5. 生成回答时区分本地知识库来源和外部网页来源。

完成标准：

- Web Search 不是孤立工具，而是进入统一 evidence pipeline。
- Trace 中能展示工具调用、外部来源和融合结果。

### 第五阶段：Phase E Agentic 架构升级

目标：

- 从固定 Workflow-first 进一步升级为受控 Agentic Workflow。

任务：

1. 抽象 Planner / Executor / Verifier。
2. 定义 `PlanStep` 和 `ToolResult`。
3. 复杂问题进入受控循环，简单问题仍走稳定 pipeline。
4. 限制 `max_steps`、`max_tool_calls`，避免死循环。
5. 每一步写入 Trace。

完成标准：

- 系统具备受控 Agent Loop，但仍保持企业级稳定性和可观测性。

### 第六阶段：Phase F 企业安全与工程化

目标：

- 让系统从“可演示”升级为“可解释、可管控、可上线”。

任务：

1. 文档级 ACL 与 chunk 权限继承。
2. 检索阶段 metadata permission filter。
3. Prompt Injection 防护。
4. 会话锁、限流、异常兜底。
5. Trace 持久化与 Langfuse / LangSmith / Phoenix 选型。

完成标准：

- 普通用户只能检索有权限的文档。
- 系统异常时可降级，不会直接中断主流程。

### 第七阶段：Phase G 高级增强能力

目标：

- 在主链路稳定后补充高阶能力。

任务：

1. GraphRAG：实体抽取、关系抽取、社区摘要、路径检索。
2. 长期记忆：用户偏好、事实记忆、跨会话记忆检索。
3. Multi-agent：将工具拆分给专业化 agent。

完成标准：

- 只在评估闭环、可信生成和检索增强稳定后推进。
- 每个高级能力都必须有对应评估或可观测指标。

## 7. 面试表达版本

如果面试官问：“你的项目距离真正 Agentic RAG 还有什么差距？”

可以回答：

> 当前系统已经实现了 Agentic RAG 的基础闭环，包括 Query Router、Hybrid Retrieval、Rerank、Quality Check、Query Rewrite、Sub-question Decomposition 和 Multi-hop Retrieval。复杂问题会先被识别为 multi_step，然后拆成多个子问题分别检索，最后合并证据进入生成阶段。
>
> 但我不会把它夸大成完全成熟的 Agentic RAG。它目前更接近 query planning + multi-query retrieval，距离主流更完整的 Agentic RAG 还有几个差距：第一，子问题之间还没有 evidence-aware 的迭代依赖；第二，还没有先生成子答案再综合的 decompose-and-synthesize；第三，缺少生成后的 grounding / faithfulness 检查；第四，评估体系还主要停留在检索评估，没有端到端 RAG 评估；第五，工具调用、文档级权限和 Guardrails 还需要补齐。
>
> 所以后续我会优先做三个方向：先把子答案综合做完整，再加 grounding 检查降低幻觉，最后用固定评估集做 Dense、BM25、Hybrid、Rerank 的消融实验，用指标证明每个模块的收益。

## 8. 当前最推荐下一步

最推荐下一步不是继续堆新功能，而是先完成：

```text
Phase A：RAGAS / 评估闭环
```

原因：

- 没有评估基线，后续 HyDE、Grounding、Web Search、GraphRAG 都无法证明收益。
- 企业级 RAG 优化应当是 evaluation-driven，而不是 feature-driven。
- 当前项目已经具备 RAGAS 最小入口，最短路径是把 baseline 稳定跑通。
- 跑通后再做子答案综合、Grounding 和 HyDE，才能形成可量化的优化闭环。

当前执行目标：

```text
1. 固定 RAGAS 可用版本和脚本命令。
2. 跑通 10 条样本 baseline。
3. 输出 ragas_scores.json 和 ragas_report.md。
4. 记录 faithfulness、context_precision、context_recall。
5. 将 baseline 作为后续大功能优化的对照组。
```

完成 Phase A 后，再进入：

```text
Phase B：子答案综合 + Grounding
```
