"""Retrieval evaluation for the Chinese finance RAG knowledge base.

The golden set stores stable evidence anchors: source PDF paths, page numbers,
and evidence text. This evaluator maps retrieved nodes back to those anchors at
runtime, so metrics remain comparable when chunking or node ids change.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any, Callable, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.postprocessor.dashscope_rerank import DashScopeRerank

from app.config import settings as app_settings
from app.rag_milvus import build_milvus_index_and_storage
from app.retrieval.auto_merging_context import AutoMergingContextPostprocessor
from app.retrieval.bm25_state import build_stateful_bm25_retriever
from app.retrieval.parent_context import ParentContextPostprocessor
from app.retrieval.table_context import TableContextPostprocessor
from app.storage.parent_store import build_parent_store


DEFAULT_QUESTION_PATH = Path("data/eval_cn_finance/golden_questions.jsonl")
DEFAULT_OUTPUT_DIR = Path("data/eval_cn_finance/results")
DEFAULT_STRATEGIES = ("hybrid",)
DEFAULT_K_VALUES = (1, 3, 5, 10)


@dataclass
class FinanceEvalQuestion:
    id: str
    question: str
    answer: str
    gold_files: list[str]
    gold_pages: list[int]
    gold_evidence: str
    difficulty: str
    question_type: str
    raw: dict[str, Any]


@dataclass
class RetrievalResult:
    nodes: list[NodeWithScore]
    latency_ms: float
    error: str = ""


@dataclass
class MatchResult:
    matched: bool
    match_type: str = ""
    file_match: bool = False
    page_match: bool = False
    text_overlap: float = 0.0
    anchor_ids: list[str] | None = None


@dataclass(frozen=True)
class EvidenceAnchor:
    id: str
    gold_file: str
    gold_page: int | None = None


NodeQrels = dict[str, dict[str, set[str]]]


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def _load_jsonl(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
            if limit and len(records) >= limit:
                break
    return records


def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _as_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    raw_values = value if isinstance(value, list) else [value]
    result: list[int] = []
    for item in raw_values:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def load_questions(path: Path, *, limit: int | None = None) -> list[FinanceEvalQuestion]:
    questions: list[FinanceEvalQuestion] = []
    for record in _load_jsonl(path, limit=limit):
        question = str(record.get("question") or "").strip()
        gold_files = _as_str_list(record.get("gold_files"))
        if not question or not gold_files:
            continue
        questions.append(
            FinanceEvalQuestion(
                id=str(record.get("id") or f"q_{len(questions) + 1}"),
                question=question,
                answer=str(record.get("answer") or ""),
                gold_files=gold_files,
                gold_pages=_as_int_list(record.get("gold_pages")),
                gold_evidence=str(record.get("gold_evidence") or ""),
                difficulty=str(record.get("difficulty") or "unknown"),
                question_type=str(record.get("question_type") or "unknown"),
                raw=record,
            )
        )
    return questions


def load_node_qrels(
    path: Path | None,
    *,
    qrel_role: str = "any",
    min_relevance: int = 1,
) -> NodeQrels:
    if path is None or not path.exists():
        return {}
    qrels: NodeQrels = defaultdict(lambda: defaultdict(set))
    with path.open("r", encoding="utf-8") as f:
        header = f.readline().strip().split("\t")
        if "query_id" not in header or "node_id" not in header:
            raise ValueError(f"node qrels must contain query_id and node_id columns: {path}")
        indexes = {name: index for index, name in enumerate(header)}
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < len(header):
                continue
            query_id = parts[indexes["query_id"]]
            node_id = parts[indexes["node_id"]]
            evidence_id = parts[indexes.get("evidence_id", indexes["node_id"])] if "evidence_id" in indexes else node_id
            relevance = 1
            if "relevance" in indexes:
                try:
                    relevance = int(float(parts[indexes["relevance"]]))
                except ValueError:
                    relevance = 1
            if relevance < min_relevance:
                continue
            if qrel_role != "any" and "qrel_role" in indexes:
                if parts[indexes["qrel_role"]] != qrel_role:
                    continue
            if query_id and evidence_id and node_id:
                qrels[query_id][evidence_id].add(node_id)
    return {query_id: dict(evidence_map) for query_id, evidence_map in qrels.items()}


def normalize_path(value: str) -> str:
    value = str(value or "").strip().replace("\\", "/")
    value = re.sub(r"/+", "/", value)
    docs_dir = str(app_settings.docs_dir or "").replace("\\", "/").strip("/")
    if docs_dir and value.startswith(docs_dir + "/"):
        value = value[len(docs_dir) + 1 :]
    return value.strip("/")


def _node_source_paths(node: NodeWithScore) -> list[str]:
    metadata = dict(node.node.metadata or {})
    candidates = [
        metadata.get("source_path"),
        metadata.get("parent_source_path"),
        metadata.get("file_path"),
        metadata.get("doc_id"),
    ]
    paths: list[str] = []
    for candidate in candidates:
        normalized = normalize_path(str(candidate or ""))
        if normalized:
            paths.append(normalized)
    return list(dict.fromkeys(paths))


def _path_matches(candidate: str, gold_files: Iterable[str]) -> bool:
    candidate = normalize_path(candidate)
    for gold in gold_files:
        gold_norm = normalize_path(gold)
        if candidate == gold_norm:
            return True
        if candidate.endswith("/" + gold_norm) or gold_norm.endswith("/" + candidate):
            return True
    return False


def evidence_anchors(question: FinanceEvalQuestion) -> list[EvidenceAnchor]:
    pages = question.gold_pages
    anchors: list[EvidenceAnchor] = []
    if pages and len(pages) == len(question.gold_files):
        for index, (gold_file, page) in enumerate(zip(question.gold_files, pages), start=1):
            anchors.append(EvidenceAnchor(id=f"a{index}", gold_file=gold_file, gold_page=page))
    elif pages:
        for index, gold_file in enumerate(question.gold_files, start=1):
            for page in pages:
                anchors.append(EvidenceAnchor(id=f"a{index}_p{page}", gold_file=gold_file, gold_page=page))
    else:
        for index, gold_file in enumerate(question.gold_files, start=1):
            anchors.append(EvidenceAnchor(id=f"a{index}", gold_file=gold_file, gold_page=None))
    return anchors


def parse_page_range(value: Any) -> set[int]:
    text = str(value or "").strip()
    if not text:
        return set()
    pages: set[int] = set()
    for part in re.split(r"[,，;；\s]+", text):
        part = part.strip()
        if not part:
            continue
        range_match = re.search(r"(\d+)\s*[-–—]\s*(\d+)", part)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start > end:
                start, end = end, start
            pages.update(range(start, end + 1))
            continue
        number_match = re.search(r"\d+", part)
        if number_match:
            pages.add(int(number_match.group(0)))
    return pages


def _node_pages(node: NodeWithScore) -> set[int]:
    metadata = dict(node.node.metadata or {})
    pages: set[int] = set()
    for key in (
        "page_range",
        "parent_page_range",
        "table_page_range",
        "parse_page_range",
        "page_label",
        "page",
        "page_no",
    ):
        pages.update(parse_page_range(metadata.get(key)))
    return pages


def _node_match_ids(node: NodeWithScore) -> set[str]:
    metadata = dict(node.node.metadata or {})
    candidates = [
        node.node.node_id,
        metadata.get("chunk_id"),
        metadata.get("child_chunk_id"),
        metadata.get("parent_id"),
    ]
    return {str(item) for item in candidates if item}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def char_ngrams(text: str, n: int = 3) -> set[str]:
    text = normalize_text(text)
    if not text:
        return set()
    if len(text) <= n:
        return {text}
    return {text[index : index + n] for index in range(0, len(text) - n + 1)}


def evidence_overlap(gold_evidence: str, retrieved_text: str) -> float:
    gold = char_ngrams(gold_evidence)
    retrieved = char_ngrams(retrieved_text)
    if not gold or not retrieved:
        return 0.0
    return len(gold & retrieved) / min(len(gold), len(retrieved))


def match_node(
    node: NodeWithScore,
    question: FinanceEvalQuestion,
    *,
    text_overlap_threshold: float,
) -> MatchResult:
    anchors = evidence_anchors(question)
    source_paths = _node_source_paths(node)
    node_pages = _node_pages(node)
    overlap = evidence_overlap(question.gold_evidence, node.node.get_content() or "")
    matched_anchor_ids: list[str] = []
    any_file_match = False
    any_page_match = False

    for anchor in anchors:
        anchor_file_match = any(_path_matches(path, [anchor.gold_file]) for path in source_paths)
        anchor_page_match = bool(anchor.gold_page is not None and node_pages and anchor.gold_page in node_pages)
        any_file_match = any_file_match or anchor_file_match
        any_page_match = any_page_match or anchor_page_match

        if anchor_file_match and anchor_page_match:
            matched_anchor_ids.append(anchor.id)
            continue
        if anchor_file_match and anchor.gold_page is None:
            matched_anchor_ids.append(anchor.id)
            continue
        if anchor_file_match and overlap >= text_overlap_threshold:
            matched_anchor_ids.append(anchor.id)

    if matched_anchor_ids:
        if any_page_match:
            match_type = "file_page"
        elif any_file_match and not any(anchor.gold_page for anchor in anchors):
            match_type = "file_only"
        else:
            match_type = "file_text_overlap"
        return MatchResult(
            True,
            match_type,
            any_file_match,
            any_page_match,
            overlap,
            list(dict.fromkeys(matched_anchor_ids)),
        )
    if not any_file_match and overlap >= max(0.75, text_overlap_threshold):
        return MatchResult(True, "text_overlap", any_file_match, any_page_match, overlap, ["text_overlap"])
    return MatchResult(False, "", any_file_match, any_page_match, overlap, [])


def match_node_by_qrels(node: NodeWithScore, question_id: str, node_qrels: NodeQrels) -> MatchResult:
    evidence_map = node_qrels.get(question_id) or {}
    node_ids = _node_match_ids(node)
    matched_evidence_ids = [
        evidence_id
        for evidence_id, positive_node_ids in evidence_map.items()
        if node_ids & positive_node_ids
    ]
    if not matched_evidence_ids:
        return MatchResult(False, "node_qrels", False, False, 0.0, [])
    return MatchResult(True, "node_qrels", True, True, 1.0, matched_evidence_ids)


def hit_at_k(matches: list[MatchResult], k: int) -> float:
    return 1.0 if any(item.matched for item in matches[:k]) else 0.0


def recall_at_k(matches: list[MatchResult], anchor_count: int, k: int) -> float:
    if anchor_count <= 0:
        return 0.0
    matched_anchor_ids = {
        anchor_id
        for match in matches[:k]
        for anchor_id in (match.anchor_ids or [])
    }
    if "text_overlap" in matched_anchor_ids:
        return 1.0
    return len(matched_anchor_ids) / anchor_count


def all_evidence_hit_at_k(matches: list[MatchResult], anchor_count: int, k: int) -> float:
    return 1.0 if recall_at_k(matches, anchor_count, k) >= 1.0 else 0.0


def mrr_at_k(matches: list[MatchResult], k: int) -> float:
    for index, match in enumerate(matches[:k]):
        if match.matched:
            return 1.0 / (index + 1)
    return 0.0


def first_hit_rank(matches: list[MatchResult]) -> int | None:
    for index, match in enumerate(matches):
        if match.matched:
            return index + 1
    return None


def _safe_retrieve(fn: Callable[[str], list[NodeWithScore]], query: str) -> RetrievalResult:
    start = time.perf_counter()
    try:
        return RetrievalResult(nodes=fn(query), latency_ms=_elapsed_ms(start))
    except Exception as exc:
        return RetrievalResult(nodes=[], latency_ms=_elapsed_ms(start), error=str(exc))


def _apply_postprocessor(
    postprocessor: BaseNodePostprocessor,
    nodes: list[NodeWithScore],
    query: str,
) -> list[NodeWithScore]:
    return postprocessor.postprocess_nodes(nodes, query_bundle=QueryBundle(query_str=query))


def _build_reranker(top_n: int) -> DashScopeRerank:
    return DashScopeRerank(
        model=app_settings.rerank_model,
        top_n=top_n,
        api_key=app_settings.qwen_llm_api_key,
    )


def _build_context_postprocessors(parent_store: Any | None) -> list[BaseNodePostprocessor]:
    if parent_store is None:
        return []
    postprocessors: list[BaseNodePostprocessor] = []
    if app_settings.auto_merging_enabled:
        postprocessors.append(
            AutoMergingContextPostprocessor(
                parent_store=parent_store,
                merge_threshold=app_settings.auto_merging_threshold,
                max_merge_chars=app_settings.auto_merging_max_chars,
            )
        )
    else:
        postprocessors.append(ParentContextPostprocessor(parent_store=parent_store))
    postprocessors.append(TableContextPostprocessor(parent_store=parent_store))
    return postprocessors


def build_strategy_retrievers(
    *,
    candidate_k: int,
    final_k: int,
    include_rerank: bool,
    include_context: bool,
) -> dict[str, Callable[[str], list[NodeWithScore]]]:
    index, storage_context = build_milvus_index_and_storage()
    parent_store = build_parent_store(app_settings.postgres_url) if app_settings.parent_store_enabled else None
    if parent_store is not None:
        all_nodes = parent_store.list_chunk_nodes()
    else:
        all_nodes = list(storage_context.docstore.docs.values())

    dense_retriever = index.as_retriever(similarity_top_k=candidate_k)
    bm25_retriever = build_stateful_bm25_retriever(
        nodes=all_nodes,
        similarity_top_k=candidate_k,
    )
    hybrid_retriever = QueryFusionRetriever(
        [dense_retriever, bm25_retriever],
        similarity_top_k=candidate_k,
        num_queries=1,
        mode="reciprocal_rerank",
        use_async=False,
    )
    context_postprocessors = _build_context_postprocessors(parent_store) if include_context else []

    def finalize(query: str, nodes: list[NodeWithScore]) -> list[NodeWithScore]:
        output = nodes
        for postprocessor in context_postprocessors:
            output = _apply_postprocessor(postprocessor, output, query)
        return output[:final_k]

    retrievers: dict[str, Callable[[str], list[NodeWithScore]]] = {
        "dense": lambda query: finalize(query, dense_retriever.retrieve(query)),
        "bm25": lambda query: finalize(query, bm25_retriever.retrieve(query)),
        "hybrid": lambda query: finalize(query, hybrid_retriever.retrieve(query)),
    }

    if include_rerank:
        reranker = _build_reranker(top_n=final_k)

        def hybrid_rerank(query: str) -> list[NodeWithScore]:
            candidates = hybrid_retriever.retrieve(query)
            for postprocessor in context_postprocessors:
                candidates = _apply_postprocessor(postprocessor, candidates, query)
            return _apply_postprocessor(reranker, candidates, query)[:final_k]

        retrievers["hybrid_rerank"] = hybrid_rerank

    return retrievers


def _node_debug_payload(node_with_score: NodeWithScore, match: MatchResult) -> dict[str, Any]:
    node = node_with_score.node
    metadata = dict(node.metadata or {})
    return {
        "node_id": str(node.node_id),
        "node_match_ids": sorted(_node_match_ids(node_with_score)),
        "score": node_with_score.score,
        "matched": match.matched,
        "match_type": match.match_type,
        "file_match": match.file_match,
        "page_match": match.page_match,
        "text_overlap": round(match.text_overlap, 4),
        "matched_anchor_ids": match.anchor_ids or [],
        "source_paths": _node_source_paths(node_with_score),
        "pages": sorted(_node_pages(node_with_score)),
        "doc_id": metadata.get("doc_id"),
        "source_path": metadata.get("source_path"),
        "page_range": metadata.get("page_range"),
        "parent_page_range": metadata.get("parent_page_range"),
        "table_id": metadata.get("table_id"),
        "table_page_range": metadata.get("table_page_range"),
        "chunk_role": metadata.get("chunk_role"),
        "retrieval_mode": metadata.get("retrieval_mode"),
        "text_preview": (node.get_content() or "")[:260],
    }


def _metrics_for_matches(matches: list[MatchResult], anchor_count: int, k_values: tuple[int, ...]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for k in k_values:
        metrics[f"hit@{k}"] = hit_at_k(matches, k)
        metrics[f"recall@{k}"] = recall_at_k(matches, anchor_count, k)
        metrics[f"all_evidence_hit@{k}"] = all_evidence_hit_at_k(matches, anchor_count, k)
        metrics[f"mrr@{k}"] = mrr_at_k(matches, k)
    metrics["mrr"] = mrr_at_k(matches, max(k_values) if k_values else len(matches))
    return metrics


def _summarize_details(details: list[dict[str, Any]], k_values: tuple[int, ...]) -> dict[str, Any]:
    if not details:
        return {}
    metric_names = sorted(details[0]["metrics"].keys())
    return {
        "question_count": len(details),
        "metrics": {
            metric: round(mean(item["metrics"][metric] for item in details), 4)
            for metric in metric_names
        },
        "first_hit_rank_avg": round(
            mean(item["first_hit_rank"] for item in details if item["first_hit_rank"] is not None),
            4,
        )
        if any(item["first_hit_rank"] is not None for item in details)
        else None,
        "hit_count_at_max_k": int(sum(item["metrics"].get(f"hit@{max(k_values)}", 0.0) for item in details)),
    }


def _group_summary(details: list[dict[str, Any]], group_key: str, k_values: tuple[int, ...]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in details:
        grouped[str(item.get(group_key) or "unknown")].append(item)
    return {key: _summarize_details(items, k_values) for key, items in sorted(grouped.items())}


def evaluate_strategy(
    *,
    name: str,
    retrieve_fn: Callable[[str], list[NodeWithScore]],
    questions: Iterable[FinanceEvalQuestion],
    k_values: tuple[int, ...],
    text_overlap_threshold: float,
    node_qrels: NodeQrels | None = None,
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    latency_values: list[float] = []
    error_count = 0

    for index, question in enumerate(questions, start=1):
        print(f"[{name}] {index}: {question.question}")
        result = _safe_retrieve(retrieve_fn, question.question)
        latency_values.append(result.latency_ms)
        if result.error:
            error_count += 1

        if node_qrels and question.id in node_qrels:
            matches = [match_node_by_qrels(node, question.id, node_qrels) for node in result.nodes]
            anchor_count = len(node_qrels[question.id])
            match_mode = "node_qrels"
        else:
            matches = [
                match_node(node, question, text_overlap_threshold=text_overlap_threshold)
                for node in result.nodes
            ]
            anchor_count = len(evidence_anchors(question))
            match_mode = "source_page_text"
        metrics = _metrics_for_matches(matches, anchor_count, k_values)
        hit_rank = first_hit_rank(matches)

        details.append(
            {
                "id": question.id,
                "question": question.question,
                "answer": question.answer,
                "difficulty": question.difficulty,
                "question_type": question.question_type,
                "gold_files": question.gold_files,
                "gold_pages": question.gold_pages,
                "gold_anchor_count": anchor_count,
                "match_mode": match_mode,
                "gold_evidence_preview": question.gold_evidence[:420],
                "latency_ms": result.latency_ms,
                "error": result.error,
                "first_hit_rank": hit_rank,
                "metrics": metrics,
                "retrieved": [
                    _node_debug_payload(node, match)
                    for node, match in zip(result.nodes, matches)
                ],
            }
        )

    metric_names = sorted(details[0]["metrics"].keys()) if details else []
    summary = {
        "strategy": name,
        "question_count": len(details),
        "error_count": error_count,
        "avg_latency_ms": round(mean(latency_values), 2) if latency_values else 0.0,
        "metrics": {
            metric: round(mean(item["metrics"][metric] for item in details), 4)
            for metric in metric_names
        },
        "by_difficulty": _group_summary(details, "difficulty", k_values),
        "by_question_type": _group_summary(details, "question_type", k_values),
        "match_type_counts": dict(Counter(
            retrieved["match_type"]
            for item in details
            for retrieved in item["retrieved"]
            if retrieved["matched"]
        )),
    }
    return {"summary": summary, "details": details}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _metric(summary: dict[str, Any], name: str) -> float:
    return float((summary.get("metrics") or {}).get(name) or 0.0)


def build_report(payload: dict[str, Any]) -> str:
    summaries = payload["summaries"]
    k_values = payload["config"]["k_values"]
    main_k = max(k_values)
    lines = [
        "# Chinese Finance Retrieval Evaluation",
        "",
        f"- Time: {payload['generated_at']}",
        f"- Dataset: `{payload['config']['question_path']}`",
        f"- Questions: {payload['question_count']}",
        f"- Candidate K: {payload['config']['candidate_k']}",
        f"- Final K: {payload['config']['final_k']}",
        f"- Match: {payload['config'].get('match_mode', 'source_path + page_range')}",
        "",
        "## Summary",
        "",
            f"| Strategy | Hit@{main_k} | Recall@{main_k} | AllEvidenceHit@{main_k} | MRR@{main_k} | Avg Latency | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for summary in summaries:
        lines.append(
            "| {strategy} | {hit:.2%} | {recall:.2%} | {all_hit:.2%} | {mrr:.2%} | {latency:.2f} ms | {errors} |".format(
                strategy=summary["strategy"],
                hit=_metric(summary, f"hit@{main_k}"),
                recall=_metric(summary, f"recall@{main_k}"),
                all_hit=_metric(summary, f"all_evidence_hit@{main_k}"),
                mrr=_metric(summary, f"mrr@{main_k}"),
                latency=float(summary.get("avg_latency_ms") or 0.0),
                errors=summary.get("error_count", 0),
            )
        )

    lines.extend(["", "## By Question Type", ""])
    for summary in summaries:
        lines.extend([
            f"### {summary['strategy']}",
            "",
            f"| Question Type | Count | Hit@{main_k} | MRR@{main_k} |",
            "|---|---:|---:|---:|",
        ])
        for group, group_summary in (summary.get("by_question_type") or {}).items():
            lines.append(
                "| {group} | {count} | {hit:.2%} | {mrr:.2%} |".format(
                    group=group,
                    count=group_summary.get("question_count", 0),
                    hit=float((group_summary.get("metrics") or {}).get(f"hit@{main_k}") or 0.0),
                    mrr=float((group_summary.get("metrics") or {}).get(f"mrr@{main_k}") or 0.0),
                )
            )
        lines.append("")

    lines.extend(
        [
            "## Notes",
            "",
        "- `Hit@K` means at least one gold evidence anchor was retrieved.",
        "- `Recall@K` measures the fraction of gold evidence anchors retrieved, which matters for cross-document multi-hop questions.",
        "- `AllEvidenceHit@K` means all gold evidence anchors were retrieved within K.",
            "- `MRR@K` rewards the first matching node being ranked earlier.",
            "- Stable PDF/page/span labels are used for dataset authoring; optional node qrels map them to current indexed chunks for stricter index-aware evaluation.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Chinese finance retrieval quality.")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTION_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--final-k", type=int, default=10)
    parser.add_argument("--k-values", default="1,3,5,10")
    parser.add_argument("--strategies", default=",".join(DEFAULT_STRATEGIES))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--text-overlap-threshold", type=float, default=0.35)
    parser.add_argument(
        "--node-qrels",
        type=Path,
        default=None,
        help="Optional node-level qrels TSV generated from the current indexed chunks.",
    )
    parser.add_argument(
        "--node-qrel-role",
        choices=("any", "primary", "secondary"),
        default="any",
        help="Filter node qrels by role when qrel_role is present.",
    )
    parser.add_argument(
        "--node-qrel-min-relevance",
        type=int,
        default=1,
        help="Minimum qrel relevance to count as relevant.",
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help="Disable parent/auto-merge/table context postprocessors during retrieval evaluation.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    k_values = tuple(sorted({int(value.strip()) for value in args.k_values.split(",") if value.strip()}))
    strategies = tuple(value.strip() for value in args.strategies.split(",") if value.strip())
    include_rerank = "hybrid_rerank" in strategies

    questions = load_questions(args.questions, limit=args.limit)
    if not questions:
        raise RuntimeError(f"No valid questions found in {args.questions}")

    print(f"Loaded {len(questions)} questions from {args.questions}")
    node_qrels = load_node_qrels(
        args.node_qrels,
        qrel_role=args.node_qrel_role,
        min_relevance=args.node_qrel_min_relevance,
    )
    if node_qrels:
        print(f"Loaded node qrels for {len(node_qrels)} questions from {args.node_qrels}")
    try:
        retrievers = build_strategy_retrievers(
            candidate_k=args.candidate_k,
            final_k=args.final_k,
            include_rerank=include_rerank,
            include_context=not args.no_context,
        )
    except Exception as exc:
        print("\nFailed to initialize retrieval components.")
        print("Please make sure the Chinese finance index and PostgreSQL parent store are ready.")
        print("Typical ingest command: .venv\\Scripts\\python.exe -m app.ingest.milvus_loader")
        print(f"Error: {exc}")
        raise SystemExit(1) from exc

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir / run_id
    results: dict[str, Any] = {}
    summaries: list[dict[str, Any]] = []

    for strategy in strategies:
        if strategy not in retrievers:
            raise ValueError(f"Unknown strategy: {strategy}. Available: {sorted(retrievers)}")
        evaluated = evaluate_strategy(
            name=strategy,
            retrieve_fn=retrievers[strategy],
            questions=questions,
            k_values=k_values,
            text_overlap_threshold=args.text_overlap_threshold,
            node_qrels=node_qrels,
        )
        results[strategy] = evaluated["details"]
        summaries.append(evaluated["summary"])

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "question_count": len(questions),
        "config": {
            "question_path": str(args.questions),
            "limit": args.limit,
            "candidate_k": args.candidate_k,
            "final_k": args.final_k,
            "k_values": k_values,
            "strategies": strategies,
            "text_overlap_threshold": args.text_overlap_threshold,
            "include_context": not args.no_context,
            "node_qrels_path": str(args.node_qrels) if args.node_qrels else "",
            "node_qrel_role": args.node_qrel_role,
            "node_qrel_min_relevance": args.node_qrel_min_relevance,
            "match_mode": "node_qrels" if node_qrels else f"source_path + page_range, fallback text overlap >= {args.text_overlap_threshold}",
        },
        "summaries": summaries,
        "results": results,
    }

    _write_json(output_dir / "retrieval_eval_results.json", payload)
    report = build_report(payload)
    (output_dir / "retrieval_eval_report.md").write_text(report, encoding="utf-8")

    latest_dir = args.output_dir / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    _write_json(latest_dir / "retrieval_eval_results.json", payload)
    (latest_dir / "retrieval_eval_report.md").write_text(report, encoding="utf-8")

    print("\n--- Chinese Finance Retrieval Evaluation Completed ---")
    print(f"Results: {output_dir / 'retrieval_eval_results.json'}")
    print(f"Report: {output_dir / 'retrieval_eval_report.md'}")
    print(f"Latest: {latest_dir / 'retrieval_eval_report.md'}")


if __name__ == "__main__":
    main()
