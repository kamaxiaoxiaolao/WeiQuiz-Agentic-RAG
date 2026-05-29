# RAG 故障排查手册

## 1. 目标

这份文档用于回答一个高频面试问题：

> 如果用户反馈 RAG 系统答案不准，你怎么定位问题出在哪一层？

WeiQuiz 的排查思路不是凭感觉调 prompt，而是沿着 RAG 链路逐层定位：

```text
文档解析
  -> 文本清洗
  -> Chunk 切分
  -> Metadata
  -> 向量 / BM25 召回
  -> RRF 融合
  -> Rerank 精排
  -> 上下文拼接
  -> LLM 生成
  -> Grounding / Reflection
```

核心原则：

> 先判断正确证据有没有进入最终 prompt，再倒推问题发生在哪一层。

## 2. 总体排查流程

用户反馈答案不准时，按下面顺序排查：

| 步骤 | 先看什么 | 目的 |
| --- | --- | --- |
| 1 | 最终答案和引用来源 | 判断答案是否有明显幻觉或引用错误 |
| 2 | final context / source nodes | 判断正确证据是否进入生成上下文 |
| 3 | Rerank 后 top_k | 判断正确 chunk 是否被精排保留下来 |
| 4 | Hybrid 候选集 | 判断 dense / BM25 是否召回过正确 chunk |
| 5 | Chunk 内容 | 判断正确内容是否被切碎或上下文不足 |
| 6 | Audit markdown | 判断文档解析后文本是否完整 |
| 7 | Ingestion report | 判断是否存在解析失败、表格/OCR/页码缺失等风险 |
| 8 | ingest_state / doc_id | 判断文档是否入库、更新或删除是否正确 |

一句话：

```text
答案错了，先看 prompt 里有没有正确证据；
有证据但答错，是生成问题；
没证据但候选里有，是 rerank/top_k 问题；
候选里没有，是召回/chunk/解析/入库问题。
```

## 3. 常见问题定位表

| 现象 | 可能原因 | 排查位置 | 处理方向 |
| --- | --- | --- | --- |
| 答案胡编，知识库没有依据 | Prompt 约束不足，未触发拒答 | trace、grounding | 加强“仅基于上下文回答”，开启 Grounding |
| 答案遗漏关键点 | final top_k 太少，parent context 不足 | source nodes、trace | 调整 top_k、启用父子块 / Auto-merging |
| 检索结果不相关 | query 表达差，召回策略不足 | retrieval trace | Query Rewrite、HyDE、Step-back、Hybrid |
| 精确词搜不到 | 只依赖向量检索 | dense/BM25 对比 | 引入 BM25、Hybrid、RRF |
| 候选里有正确 chunk，但最终没用 | Rerank 排序问题 | rerank score、final top_k | 调整 candidate_k、rerank 模型、top_n |
| 原文有答案，但 chunk 里没有 | 文档解析或清洗丢内容 | audit markdown | 修复 parser、OCR、表格解析 |
| 表格问答错误 | 表格结构丢失或跨页表格被拆断 | audit markdown、ingestion report | table block、Markdown 表格、跨页表格合并 |
| 扫描 PDF 完全搜不到 | PDF 没有文字层，未 OCR | ingestion report | OCR pipeline |
| 答案来源页码不准 | metadata 缺失或 page_range 传递错误 | source nodes、metadata_schema | 修复 metadata 继承 |
| 删除文档后仍能搜到 | 旧 chunk 残留 | ingest_state、Milvus、parent store | 删除旧 doc_id 后再重建 |
| 更新文档后答案还是旧的 | 增量索引未识别更新或旧 chunk 未删 | sha256、diff_docs、report | 检查 hash diff 和 safe_delete_doc |

## 4. 各层具体怎么看

### 4.1 文档解析层

关注文件：

```text
data/audit/parsed_md/
data/audit/section_md/
data/audit/ingestion_report_latest.json
```

重点看：

- 文档是否成功解析。
- 文本是否乱码。
- 表格是否被保留为 Markdown / HTML。
- 扫描 PDF 是否被识别。
- 页眉页脚是否大量残留。
- 目录、页码、水印是否进入正文。
- 章节标题是否保留。

如果 audit markdown 里已经没有正确内容，后面检索一定找不到。

### 4.2 Chunk 层

重点看：

- chunk 是否过短。
- chunk 是否过长。
- 是否把一个完整语义单元切断。
- 表格、代码块、步骤列表是否被切碎。
- leaf chunk 是否有对应 parent。
- parent/root 上下文是否能补全语义。

典型问题：

```text
用户问一个流程问题，但每个步骤被切到不同 chunk，最终只召回一半。
```

处理方式：

- 调整 chunk_size / overlap。
- 对标题、表格、代码块做结构化切分。
- 使用父子块策略，小块检索，大块生成。

### 4.3 Metadata 层

重点字段：

```text
doc_id
source_path
file_name
file_type
section_id
section_title
section_path
page_range
chunk_id
parent_id
chunk_role
retrieval_mode
```

metadata 的作用：

- 支撑答案引用。
- 支撑 parent context 回取。
- 支撑文档删除和更新。
- 支撑权限过滤设计。
- 支撑排查 source node 来源。

如果 metadata 不稳定，会导致：

- 来源显示错误。
- parent 找不到。
- 删除旧 chunk 失败。
- 权限过滤无法落地。

### 4.4 召回层

重点判断：

```text
正确 chunk 有没有进入候选集？
```

如果没有进入候选集，问题通常在：

- query 表达和文档表达差距大。
- embedding 模型不适合。
- chunk 切分不合理。
- BM25 缺失或分词差。
- 文档根本没入库。

排查方式：

- 分别看 dense only 和 BM25 only。
- 看 Hybrid 后候选是否包含正确 chunk。
- 看 retrieval_query 是否合理。
- 看是否需要 Query Rewrite / HyDE / Step-back。

### 4.5 RRF 融合层

RRF 关注的是排名融合，不直接比较 dense score 和 BM25 score。

排查重点：

- 正确 chunk 是否只在一路召回里出现。
- 是否被另一路强相关结果挤掉。
- candidate_k 是否太小。

如果正确 chunk 在 BM25 中排名很靠前，但融合后丢失，说明融合参数或候选池需要调整。

### 4.6 Rerank 层

重点判断：

```text
正确 chunk 是否在候选集里，但 rerank 后没有进 final top_k？
```

如果是，说明问题在 Rerank：

- rerank 模型对当前领域不敏感。
- candidate 太长或噪声太多。
- final_top_k 太小。
- query 不适合直接 rerank。

处理方式：

- 调大 candidate_k。
- 调整 final_top_k。
- 更换 rerank 模型。
- 针对复杂问题先做子问题拆解。

### 4.7 生成层

重点判断：

```text
最终 prompt 中是否已经有正确证据？
```

如果有证据但答案错了，说明是生成层问题。

常见原因：

- Prompt 没有强约束只能基于上下文回答。
- 上下文太长，模型忽略关键证据。
- 多个文档证据冲突，模型没有显式处理。
- 引用格式不明确。

处理方式：

- 加强回答规则。
- 要求信息不足时拒答。
- 对冲突证据显式说明。
- 开启 Grounding / Reflection。

### 4.8 Grounding 层

Grounding 适合检查：

- 答案中的关键结论是否被 source nodes 支撑。
- 是否有 unsupported claims。
- 是否需要降低置信度或提示信息不足。

注意：

Grounding 会增加一次 LLM 调用，所以不一定默认全部开启。更合理的策略是：

- 普通问题默认关闭。
- 复杂问题 auto 开启。
- 用户选择反思模式时强制开启。

## 5. WeiQuiz 现有可用排查材料

| 材料 | 位置 | 用途 |
| --- | --- | --- |
| RAG trace | `/chat/stream` result / message metadata | 查看 route、rewrite、quality、decomposition、grounding |
| source_nodes | assistant message metadata | 查看最终进入答案的来源 chunk |
| citations | assistant message metadata | 查看答案引用 |
| ingestion report | `data/audit/ingestion_report_latest.json` | 查看入库成功失败、解析质量、chunk 统计 |
| parsed markdown audit | `data/audit/parsed_md/` | 查看 parser 原始 block 效果 |
| section markdown audit | `data/audit/section_md/` | 查看 section 合并效果 |
| ingest state | `data/index/ingest_state.json` | 查看文档 hash、doc_id、更新时间 |
| PostgreSQL parent store | `rag_chunk_nodes` | 查看 parent/root/leaf 节点 |
| Redis memory cache | Redis | 查看最近会话窗口 |
| SessionSummary | PostgreSQL `session_summaries` | 查看摘要压缩结果 |

## 6. 面试标准答案

如果面试官问：

> 用户反馈答案不准，你怎么排查？

可以回答：

> 我会按 RAG 链路倒查。第一步先看最终 prompt 或 source nodes 里有没有正确证据。如果有正确证据但模型答错，说明是生成层或 prompt 约束问题，可以加强引用规则、拒答规则或开启 Grounding。如果最终上下文里没有正确证据，我会继续看 rerank 前的候选集；如果候选里有但 rerank 后没进 top_k，说明是 rerank 或 top_k 问题。如果候选里也没有，就分别看 dense、BM25、hybrid 的召回结果，判断是语义召回、关键词召回还是 query 表达问题。如果 chunk 本身内容不完整，就回到 chunk 策略；如果 audit markdown 里都没有正确文本，就说明是文档解析或清洗出了问题。我们项目里有 trace、source nodes、ingestion report 和 audit markdown，所以可以比较系统地定位问题。

## 7. 最常见的工程结论

RAG 答案不准通常不是单点问题，而是链路问题。

常见排序：

```text
文档解析质量差
  > chunk 切分不合理
  > 召回没命中
  > rerank 排错
  > prompt / generation 幻觉
```

因此优化顺序也应该是：

```text
先保证数据进来是对的
再保证正确内容能被召回
再保证正确内容能排到前面
最后再优化生成 prompt
```

