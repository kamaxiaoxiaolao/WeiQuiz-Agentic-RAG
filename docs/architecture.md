# Architecture

WeiQuiz is organized around one goal: make the RAG answer path explainable and easy to improve.

## Request Flow

```text
User message
  -> FastAPI /chat/stream
  -> JWT authentication and session ownership check
  -> MemoryService builds recent context and session summary
  -> LongTermMemoryService optionally searches durable memories
  -> AgentController decides execution mode
  -> RAG workflow or tool-call workflow
  -> SSE stream returns route, steps, answer chunks, citations, and trace
  -> Conversation and metadata are persisted
```

## Agent Controller

The controller is the decision layer. It does not retrieve documents or generate the final answer. It returns a structured decision:

- `mode`: chitchat, RAG workflow, tool call, or clarification.
- `route`: intent, method, confidence, strategy, and reason.
- `memory_policy`: which memory layers should be used.
- `tool_plan`: optional function-call plan.
- `rag_strategy`: direct, decomposition, HyDE, step-back, web search, SQL, or chitchat.
- `need_grounding`: whether to run evidence support checks.
- `max_retries`: retry budget for rewrite/retrieval.

This separation keeps routing decisions testable and prevents the API layer from becoming a large conditional block.

## RAG Workflow

```text
Route
  -> Optional query transform
      -> Decomposition for broad or comparative questions
      -> HyDE for weak semantic queries
      -> Step-back for abstract background questions
  -> Retrieval
  -> Quality check
  -> Optional rewrite and retry
  -> Intermediate synthesis for multi-step paths
  -> Final answer generation
  -> Optional grounding
```

The workflow emits step events so the frontend can show what happened during a response.

## Retrieval Pipeline

```text
Query
  -> Dense vector retriever
  -> Stateful BM25 retriever
  -> RRF fusion
  -> Parent context / auto-merging / table context
  -> Rerank
  -> SourceNodePayload
```

Dense retrieval improves semantic recall. BM25 improves exact term, code, policy-number, and named-entity recall. RRF avoids directly comparing scores from different retrieval methods. Rerank is used as a second-stage precision pass.

## Hierarchical Chunking

WeiQuiz uses hierarchical nodes:

- Root: broad document-level context.
- Parent: generation-sized context.
- Leaf: retrieval-sized chunk.

Leaf nodes are optimized for search precision. Parent and root nodes preserve context for generation. When enough sibling leaf nodes are hit, auto-merging can replace them with a parent-level context block.

## Ingestion Pipeline

```text
Scan docs_dir
  -> SHA256 diff
  -> Parse file into blocks
  -> Clean and normalize blocks
  -> Merge or split tables when needed
  -> Build section documents
  -> Build hierarchical nodes
  -> Store leaf nodes in vector backend
  -> Store parent/root context in PostgreSQL
  -> Update BM25 state
  -> Write ingestion report
```

The ingestion report is important for debugging: many RAG failures begin as parsing, chunking, or metadata failures.

## Memory Layers

| Layer | Storage | Purpose |
| --- | --- | --- |
| Recent memory | Redis or process fallback | Short-term conversational context |
| Full history | PostgreSQL | Complete durable audit trail |
| Session summary | PostgreSQL | Rolling compression for long sessions |
| Long-term memory | Optional Mem0 | Cross-session user preferences and stable facts |

The answer prompt receives a compact memory context instead of the full message history.

## API Surface

Main groups:

- `/auth/*`: register, login, current user.
- `/chat/stream`: SSE chat response.
- `/sessions/*`: user session lifecycle.
- `/documents/*`: upload, reindex, delete, and library view.
- `/admin/*`: users, audit logs, and overview.
- `/debug/*`: memory inspection and manual compression helpers.

## Runtime Services

Local development uses:

- FastAPI for backend APIs.
- Vue/Vite for frontend.
- PostgreSQL for users, sessions, summaries, audit logs, and parent chunk context.
- Redis for recent memory and transient job state.
- Chroma by default for vector storage.
- Optional Milvus for production-like vector service.
- Optional Phoenix for tracing.

## Failure Modes to Inspect

- Bad answer with good sources: likely generation or grounding prompt issue.
- Bad answer with bad sources: inspect retrieval, rerank, BM25 state, and query strategy.
- Empty answer: inspect vector index, embedding config, document ingestion status, and `TOP_K`.
- Missing context: inspect chunk sizes, parent store, and auto-merging.
- Wrong user data: inspect auth dependencies and session ownership checks.
