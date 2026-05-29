# WeiQuiz RAG 系统评估计划书

版本：v2.1  
更新时间：2026-05-20  
当前阶段：P0 检索评估  
当前结论：先验证检索，不做生成评估，不接 RAGAS。

---

## 1. 当前为什么只做检索评估

WeiQuiz 当前项目阶段在 RAG 数据预处理、文档切分、索引构建和检索优化附近。这个阶段最重要的问题不是“最终回答像不像人”，而是：

1. 文档有没有被正确解析和切分。
2. chunk 能不能被正确写入索引。
3. query 能不能召回包含答案的 evidence chunk。
4. hybrid、BM25、dense、rerank 是否真的提升召回和排序。

所以当前评估只回答一个问题：

> 给定 query，系统能不能把包含答案的证据 chunk 召回到 Top K？

当前暂不做：

- Faithfulness
- Answer Relevance
- LLM-as-judge
- RAGAS
- 端到端答案准确率
- 人工答案评分

原因是：如果正确证据都没有被召回，生成评估没有意义。先把 retrieval 做扎实，再评估 generation。

---

## 2. 为什么当前需要公开数据集

当前项目自建知识库规模较小，文档复杂度不够，难以覆盖真实 RAG 检索中的复杂情况，比如：

- 同义表达
- 长上下文段落
- 相似干扰段落
- 答案分布在不同位置
- query 和 evidence 字面不完全匹配
- 不同 chunk 切分策略对召回的影响

因此当前引入公开文档型数据集，不是为了证明最终业务效果，而是为了获得更复杂、可复现、带标准证据的文档环境。

面试表达：

> 项目早期业务知识库较小，如果只用自己的文档评估，问题会过于简单，无法暴露检索链路的问题。所以我引入公开文档型数据集作为 retrieval benchmark，用来验证 dense、BM25、hybrid、rerank 等检索策略。公开数据集不是最终业务效果证明，后续仍然要回到业务 Golden Set 做验证。

---

## 3. P0 数据集选择：SQuAD v1.1

当前优先选择 SQuAD v1.1。

原因：

1. 它有较完整的 paragraph context，适合模拟“文档入库”。
2. 它有 question，适合作为检索 query。
3. 它有 answer text，可以定位 gold evidence。
4. 它比 MS MARCO 更像文档问答 RAG。
5. 它比 HotpotQA 简单，适合第一阶段检索评估。

第一阶段不优先选择：

| 数据集 | 暂不优先原因 |
| --- | --- |
| MS MARCO | 更偏搜索排序，passage 较短，不太像企业文档问答 |
| HotpotQA | 多跳复杂度高，适合 P1/P2，不适合第一版 |
| Natural Questions | 开放域 QA 噪声更高，第一版排查成本较大 |
| BEIR | 适合后续横向 benchmark，第一版成本略高 |
| RAGAS WikiQA | 更偏生成评估和 RAGAS 示例，不是当前重点 |

---

## 4. SQuAD 检索评估构造方式

### 4.1 文档构造

从 SQuAD 中抽取一批 paragraph context，把每个 context 当成一篇小文档或一个文档段落入库。

建议第一版规模：

```text
contexts: 100-300 个
questions: 100-300 条
```

文档元数据建议：

```json
{
  "doc_id": "squad_doc_0001",
  "title": "University_of_Notre_Dame",
  "paragraph_id": "squad_para_0001",
  "source": "squad_v1"
}
```

### 4.2 Query 构造

SQuAD 的 `question` 直接作为 query。

示例：

```json
{
  "id": "squad_q0001",
  "query": "To whom did the Virgin Mary allegedly appear in 1858 in Lourdes France?",
  "gold_doc": "squad_doc_0001",
  "gold_paragraph_id": "squad_para_0001",
  "gold_answer": "Saint Bernadette Soubirous",
  "gold_evidence": "Architecturally, the school has a Catholic character..."
}
```

### 4.3 Gold Evidence 定义

第一版 gold evidence 使用 paragraph 级别，而不是 chunk 级别。

原因：

- chunk_id 会随着 chunk_size、overlap、parser 改变。
- paragraph_id 更稳定。
- 第一阶段目标是评估正确段落是否能被召回。

命中判断：

```text
如果 Top K retrieved chunks 中任意 chunk 的 paragraph_id == gold_paragraph_id，则视为命中。
```

如果 metadata 暂时没有 paragraph_id，可以退化为：

```text
retrieved chunk text 包含 gold_answer
或 retrieved chunk text 与 gold_evidence 有足够重叠
```

但推荐优先做 metadata 级命中，评估更稳定。

---

## 5. 当前只做的指标

### 5.1 Hit@K

含义：

> Top K 结果里是否至少有一个命中 gold paragraph。

公式：

```text
Hit@K = 1 if gold_paragraph_id in retrieved_top_k else 0
```

用途：

- 判断正确证据有没有被召回。
- 第一版最重要。

### 5.2 Recall@K

SQuAD 第一版通常一个问题对应一个 gold paragraph，所以 Recall@K 基本等价于 Hit@K。

后续如果引入多证据问题，再扩展为：

```text
Recall@K = 命中的 gold evidence 数 / 总 gold evidence 数
```

### 5.3 MRR@K

含义：

> 第一个正确证据排得越靠前，分数越高。

公式：

```text
MRR@K = 1 / rank
```

如果 Top K 没命中，则为 0。

用途：

- 衡量排序质量。
- 用来判断 rerank 是否真的有收益。

### 5.4 nDCG@K

第一版可以暂缓，P1 再做。

原因：

- SQuAD 第一版多为单 gold paragraph。
- nDCG 更适合多相关证据排序。

### 5.5 Latency

记录检索耗时：

```text
avg_latency_ms
p50_latency_ms
p95_latency_ms
```

用途：

- 避免只追求召回，忽视性能。
- 对比 hybrid 和 rerank 的额外成本。

---

## 6. P0 对比策略

第一版只比较检索策略，不比较生成策略。

| 版本 | 策略 | 目的 |
| --- | --- | --- |
| Baseline | Dense only | 建立语义召回基线 |
| V1 | BM25 only | 验证关键词召回能力 |
| V2 | Dense + BM25 + RRF | 验证 hybrid 是否提升召回 |
| V3 | Hybrid + Rerank | 验证 rerank 是否提升排序 |

每个策略固定同一批 SQuAD query 跑评估。

报告字段：

```json
{
  "strategy": "hybrid_rerank",
  "dataset": "squad_v1",
  "sample_count": 100,
  "hit_at_5": 0.0,
  "recall_at_5": 0.0,
  "mrr_at_5": 0.0,
  "avg_latency_ms": 0.0,
  "p95_latency_ms": 0.0,
  "config": {
    "chunk_size": 512,
    "chunk_overlap": 80,
    "vector_top_k": 4,
    "bm25_top_k": 4,
    "fusion_top_k": 10,
    "rerank_top_n": 3,
    "final_top_k": 5
  }
}
```

注意：文档里不要提前写虚假的提升数字。只有真实跑出结果后，才能写 Recall@5 从 X 到 Y。

---

## 7. 评估目录规划

推荐结构：

```text
data/eval/
  squad/
    squad_contexts.jsonl
    squad_queries.jsonl
  reports/
    retrieval_squad_dense_YYYYMMDD.json
    retrieval_squad_bm25_YYYYMMDD.json
    retrieval_squad_hybrid_YYYYMMDD.json
    retrieval_squad_hybrid_rerank_YYYYMMDD.json
    badcase_squad_YYYYMMDD.md

app/eval/
  retrieval_eval.py
  metrics.py
  prepare_squad_eval.py
  compare_retrievers.py
```

当前项目已有 `app/eval/retrieval_eval.py`，下一步优先补：

1. SQuAD 数据准备脚本。
2. paragraph_id metadata 写入。
3. Hit@K / MRR@K 计算。
4. 多策略对比报告。

---

## 8. P0 实施步骤

### Step 1：准备 SQuAD 子集

任务：

- 下载或读取 SQuAD v1.1。
- 抽取 100-300 个 context。
- 为每个 context 分配 `doc_id` 和 `paragraph_id`。
- 为每个 question 生成 query 样本。

输出：

```text
data/eval/squad/squad_contexts.jsonl
data/eval/squad/squad_queries.jsonl
```

### Step 2：将 context 入库

任务：

- 把 SQuAD context 当作文档入库。
- 保留 `source=squad_v1`、`doc_id`、`paragraph_id`、`title` metadata。
- 确认 chunk 继承 paragraph_id。

验收：

```text
检索结果 source_nodes 中可以看到 paragraph_id。
```

### Step 3：实现检索评估

任务：

- 对每条 query 执行检索。
- 取 Top K source nodes。
- 判断是否命中 gold_paragraph_id。
- 计算 Hit@5、MRR@5、Latency。

第一版可以只做：

```text
Hit@5
MRR@5
avg_latency_ms
p95_latency_ms
```

### Step 4：输出 badcase

每条失败样本保存：

```text
query
gold_answer
gold_paragraph_id
retrieved paragraph_ids
retrieved texts
rank
strategy
```

badcase 用来判断：

- 是 chunk 切分问题？
- 是 embedding 语义召回问题？
- 是 BM25 关键词问题？
- 是 RRF 融合问题？
- 是 rerank 排序问题？
- 是 metadata 没保留下来？

### Step 5：多策略对比

同一批 query 分别跑：

```text
dense only
bm25 only
hybrid
hybrid + rerank
```

输出对比表：

```markdown
| strategy | Hit@5 | MRR@5 | avg_latency_ms | p95_latency_ms |
| --- | --- | --- | --- | --- |
| dense_only | 待跑 | 待跑 | 待跑 | 待跑 |
| bm25_only | 待跑 | 待跑 | 待跑 | 待跑 |
| hybrid | 待跑 | 待跑 | 待跑 | 待跑 |
| hybrid_rerank | 待跑 | 待跑 | 待跑 | 待跑 |
```

---

## 9. 后续阶段暂缓项

这些重要，但不属于当前 P0：

### P1：复杂检索评估

- HotpotQA 多跳问题
- BEIR / SciFact
- hard negative
- nDCG@K
- metadata filter 评估

### P2：生成评估

- Faithfulness
- Answer Relevance
- Citation Accuracy
- Refusal Accuracy
- RAGAS / LlamaIndex Evaluators

### P3：线上观测

- Phoenix
- Langfuse
- 用户反馈
- 线上 badcase 回流

---

## 10. 面试表达

### Q1：你为什么先做检索评估，而不是生成评估？

回答：

> 当前项目阶段主要在文档切分、索引构建和检索优化。RAG 的上限首先取决于正确证据能不能被召回，如果 evidence 没进 Top K，后面的生成模型再强也无法稳定回答。所以我第一阶段只做 retrieval evaluation，重点看 Hit@K、MRR@K 和检索延迟，先比较 dense only、BM25、hybrid、hybrid + rerank 的效果。

### Q2：为什么用公开数据集？

回答：

> 因为项目早期业务知识库规模较小，问题比较简单，无法覆盖复杂检索场景。我引入公开文档型数据集，不是为了证明最终业务效果，而是为了构造一个更复杂、可复现、带标准证据的检索 benchmark。第一版我选择 SQuAD，因为它有 paragraph context、question 和 answer text，适合构造 paragraph-level retrieval evaluation。

### Q3：SQuAD 怎么变成 RAG 检索评估？

回答：

> 我把 SQuAD 的 paragraph context 当作文档入库，把 question 当作 query，把 answer 所在 paragraph 标为 gold evidence。检索时只看 Top K chunk 是否来自对应 gold paragraph。这样可以计算 Hit@5 和 MRR@5，用来比较不同检索策略是否真的把正确证据召回并排到更靠前的位置。

### Q4：如果 Hit@5 低，你怎么排查？

回答：

> 我会先看 badcase。如果 Top K 完全没有 gold paragraph，优先检查文档解析、chunk 切分、metadata 继承和 top_k；如果 gold paragraph 被召回但排名靠后，重点看 RRF 融合和 rerank；如果只有关键词强相关的问题失败，说明 dense 召回不足，需要 BM25 或 hybrid；如果语义改写类问题失败，后续再引入 query rewrite。

---

## 11. 当前下一步

当前已完成：

> 已新增 SQuAD 检索评估数据准备脚本 `app/eval/prepare_squad_eval.py`，可生成 `docs/`、`squad_contexts.jsonl`、`squad_queries.jsonl` 和 `manifest.json`。

本地样例验证命令：

```bash
python app/eval/prepare_squad_eval.py \
  --input-json data/eval/squad_sample.json \
  --output-root data/eval/squad_demo \
  --queries 2 \
  --clean
```

真实 SQuAD 子集生成命令：

```bash
python app/eval/prepare_squad_eval.py \
  --output-root data/eval/squad \
  --queries 100 \
  --split validation \
  --clean
```

下一步只做一件事：

> 将 SQuAD context 入库，并确认 chunk metadata 中保留 `paragraph_id`。
