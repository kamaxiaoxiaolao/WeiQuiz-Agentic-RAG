"""Retrieval ablation evaluator for the WeiQuiz RAG pipeline.

This script compares retrieval strategies on the same labeled questions:

- dense: vector retriever only
- bm25: BM25 retriever only
- hybrid: dense + BM25 with RRF fusion
- hybrid_rerank: hybrid retrieval followed by DashScope rerank

It intentionally evaluates retrieval only, not answer generation. This makes
the report useful for tuning chunking, top_k, fusion, and rerank before adding
generation-level metrics such as faithfulness or answer relevance.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
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
from app.retrieval.bm25_state import build_stateful_bm25_retriever
from app.storage.parent_store import build_parent_store


DEFAULT_QUESTION_PATH = Path("data/eval/squad/squad_queries.jsonl")
DEFAULT_OUTPUT_DIR = Path("data/eval/retrieval_runs")
DEFAULT_STRATEGIES = ("dense", "bm25", "hybrid", "hybrid_rerank")
DEFAULT_K_VALUES = (1, 3, 5, 10)


@dataclass
class EvalQuestion:
    id: str
    question: str
    gold_ids: list[str]
    gold_level: str
    raw: dict[str, Any]


@dataclass
class RetrievalResult:
    nodes: list[NodeWithScore]
    latency_ms: float
    error: str = ""


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


def _first_list(*values: Any) -> list[str]:
    result: list[str] = []
    for value in values:
        if not value:
            continue
        if isinstance(value, list):
            result.extend(str(item) for item in value if item)
        else:
            result.append(str(value))
    return [item for item in result if item]


def _normalize_question(record: dict[str, Any]) -> EvalQuestion | None:
    metadata = record.get("metadata") or {}
    evidence = record.get("gold_evidence") or []

    paragraph_ids = _first_list(
        record.get("gold_paragraph_id"),
        record.get("gold_paragraph_ids"),
        metadata.get("gold_paragraph_ids"),
    )
    if paragraph_ids:
        gold_ids = paragraph_ids
        gold_level = "paragraph_id"
    else:
        evidence_doc_ids = [item.get("doc_id") for item in evidence if isinstance(item, dict)]
        gold_ids = _first_list(
            record.get("gold_doc_id"),
            record.get("gold_doc_ids"),
            metadata.get("beir_gold_doc_ids"),
            evidence_doc_ids,
        )
        gold_level = "doc_id"

    question = str(record.get("question") or record.get("query") or record.get("user_input") or "").strip()
    if not question or not gold_ids:
        return None

    return EvalQuestion(
        id=str(record.get("id") or len(question)),
        question=question,
        gold_ids=list(dict.fromkeys(gold_ids)),
        gold_level=gold_level,
        raw=record,
    )


def load_questions(path: Path, *, limit: int | None = None) -> list[EvalQuestion]:
    records = _load_jsonl(path, limit=limit)
    questions = [_normalize_question(record) for record in records]
    return [question for question in questions if question is not None]


def _node_identifier(node_with_score: NodeWithScore, level: str) -> str:
    node = node_with_score.node
    metadata = dict(node.metadata or {})
    if level == "paragraph_id":
        return str(metadata.get("paragraph_id") or metadata.get("gold_paragraph_id") or node.node_id)
    if level == "doc_id":
        return str(metadata.get("doc_id") or metadata.get("parent_doc_id") or node.node_id)
    return str(node.node_id)


def _node_debug_payload(node_with_score: NodeWithScore, level: str) -> dict[str, Any]:
    node = node_with_score.node
    metadata = dict(node.metadata or {})
    return {
        "node_id": str(node.node_id),
        "match_id": _node_identifier(node_with_score, level),
        "score": node_with_score.score,
        "doc_id": metadata.get("doc_id") or metadata.get("parent_doc_id"),
        "paragraph_id": metadata.get("paragraph_id"),
        "file_name": metadata.get("file_name"),
        "chunk_role": metadata.get("chunk_role"),
        "text_preview": (node.get_content() or "")[:240],
    }


def hit_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    return 1.0 if set(retrieved_ids[:k]) & set(gold_ids) else 0.0


def recall_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    if not gold_ids:
        return 0.0
    return len(set(retrieved_ids[:k]) & set(gold_ids)) / len(set(gold_ids))


def precision_at_k(retrieved_ids: list[str], gold_ids: list[str], k: int) -> float:
    if k <= 0:
        return 0.0
    return len(set(retrieved_ids[:k]) & set(gold_ids)) / k


def mrr(retrieved_ids: list[str], gold_ids: list[str]) -> float:
    gold = set(gold_ids)
    for index, retrieved_id in enumerate(retrieved_ids):
        if retrieved_id in gold:
            return 1.0 / (index + 1)
    return 0.0


def _safe_retrieve(fn: Callable[[str], list[NodeWithScore]], query: str) -> RetrievalResult:
    start = time.perf_counter()
    try:
        nodes = fn(query)
        return RetrievalResult(nodes=nodes, latency_ms=_elapsed_ms(start))
    except Exception as exc:
        return RetrievalResult(nodes=[], latency_ms=_elapsed_ms(start), error=str(exc))


def _build_reranker(top_n: int) -> DashScopeRerank:
    return DashScopeRerank(
        model="gte-rerank",
        top_n=top_n,
        api_key=app_settings.qwen_llm_api_key,
    )


def _apply_postprocessor(
    postprocessor: BaseNodePostprocessor,
    nodes: list[NodeWithScore],
    query: str,
) -> list[NodeWithScore]:
    return postprocessor.postprocess_nodes(nodes, query_bundle=QueryBundle(query_str=query))


def build_strategy_retrievers(
    *,
    candidate_k: int,
    final_k: int,
    include_rerank: bool,
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

    retrievers: dict[str, Callable[[str], list[NodeWithScore]]] = {
        "dense": lambda query: dense_retriever.retrieve(query)[:final_k],
        "bm25": lambda query: bm25_retriever.retrieve(query)[:final_k],
        "hybrid": lambda query: hybrid_retriever.retrieve(query)[:final_k],
    }

    if include_rerank:
        reranker = _build_reranker(top_n=final_k)

        def hybrid_rerank(query: str) -> list[NodeWithScore]:
            candidates = hybrid_retriever.retrieve(query)
            return _apply_postprocessor(reranker, candidates, query)[:final_k]

        retrievers["hybrid_rerank"] = hybrid_rerank

    return retrievers


def evaluate_strategy(
    *,
    name: str,
    retrieve_fn: Callable[[str], list[NodeWithScore]],
    questions: Iterable[EvalQuestion],
    k_values: tuple[int, ...],
) -> dict[str, Any]:
    per_question: list[dict[str, Any]] = []
    latency_values: list[float] = []
    error_count = 0

    for index, question in enumerate(questions, start=1):
        print(f"[{name}] {index}: {question.question}")
        result = _safe_retrieve(retrieve_fn, question.question)
        latency_values.append(result.latency_ms)
        if result.error:
            error_count += 1

        retrieved_ids = [_node_identifier(node, question.gold_level) for node in result.nodes]
        metrics = {
            f"hit@{k}": hit_at_k(retrieved_ids, question.gold_ids, k)
            for k in k_values
        }
        metrics.update({
            f"recall@{k}": recall_at_k(retrieved_ids, question.gold_ids, k)
            for k in k_values
        })
        metrics.update({
            f"precision@{k}": precision_at_k(retrieved_ids, question.gold_ids, k)
            for k in k_values
        })
        metrics["mrr"] = mrr(retrieved_ids, question.gold_ids)

        per_question.append(
            {
                "id": question.id,
                "question": question.question,
                "gold_level": question.gold_level,
                "gold_ids": question.gold_ids,
                "retrieved_ids": retrieved_ids,
                "latency_ms": result.latency_ms,
                "error": result.error,
                "metrics": metrics,
                "retrieved": [_node_debug_payload(node, question.gold_level) for node in result.nodes],
            }
        )

    metric_names = sorted(per_question[0]["metrics"].keys()) if per_question else []
    summary = {
        "strategy": name,
        "question_count": len(per_question),
        "error_count": error_count,
        "avg_latency_ms": round(mean(latency_values), 2) if latency_values else 0.0,
        "metrics": {
            metric: round(mean(item["metrics"][metric] for item in per_question), 4)
            for metric in metric_names
        },
    }
    return {"summary": summary, "details": per_question}


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
    generated_at = payload["generated_at"]
    question_path = payload["config"]["question_path"]

    lines = [
        "# Retrieval Ablation Report",
        "",
        f"- Time: {generated_at}",
        f"- Dataset: `{question_path}`",
        f"- Samples: {payload['question_count']}",
        f"- Candidate K: {payload['config']['candidate_k']}",
        f"- Final K: {payload['config']['final_k']}",
        "",
        "## Summary",
        "",
        "| Strategy | Hit@{} | Recall@{} | MRR | Precision@{} | Avg Latency | Errors |".format(main_k, main_k, main_k),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for summary in summaries:
        lines.append(
            "| {strategy} | {hit:.2%} | {recall:.2%} | {mrr:.2%} | {precision:.2%} | {latency:.2f} ms | {errors} |".format(
                strategy=summary["strategy"],
                hit=_metric(summary, f"hit@{main_k}"),
                recall=_metric(summary, f"recall@{main_k}"),
                mrr=_metric(summary, "mrr"),
                precision=_metric(summary, f"precision@{main_k}"),
                latency=float(summary.get("avg_latency_ms") or 0.0),
                errors=summary.get("error_count", 0),
            )
        )

    lines.extend(
        [
            "",
            "## How To Read This",
            "",
            "- Hit@K answers whether at least one gold document/chunk appears in the top K results.",
            "- Recall@K measures how many gold ids were retrieved within top K.",
            "- MRR rewards strategies that rank the first correct result higher.",
            "- Avg Latency helps judge whether a quality gain is worth the extra cost.",
            "",
            "## Interview Talking Point",
            "",
            "This report is the evidence layer behind retrieval choices. If hybrid beats dense on keyword-heavy questions, it supports using BM25 + vector search. If rerank improves MRR but increases latency, it supports conditional rerank instead of always-on rerank.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run retrieval ablation evaluation.")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTION_PATH)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--final-k", type=int, default=10)
    parser.add_argument("--k-values", default="1,3,5,10")
    parser.add_argument("--strategies", default=",".join(DEFAULT_STRATEGIES))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
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
    try:
        retrievers = build_strategy_retrievers(
            candidate_k=args.candidate_k,
            final_k=args.final_k,
            include_rerank=include_rerank,
        )
    except Exception as exc:
        print("\nFailed to initialize retrieval components.")
        print("Please make sure Milvus/PostgreSQL are running and the index has been built.")
        print("Typical setup command: docker compose up -d")
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
        },
        "summaries": summaries,
        "results": results,
    }

    _write_json(output_dir / "retrieval_ablation_results.json", payload)
    report = build_report(payload)
    (output_dir / "retrieval_ablation_report.md").write_text(report, encoding="utf-8")

    latest_dir = args.output_dir / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    _write_json(latest_dir / "retrieval_ablation_results.json", payload)
    (latest_dir / "retrieval_ablation_report.md").write_text(report, encoding="utf-8")

    print("\n--- Retrieval Ablation Completed ---")
    print(f"Report: {output_dir / 'retrieval_ablation_report.md'}")
    print(f"Latest: {latest_dir / 'retrieval_ablation_report.md'}")


if __name__ == "__main__":
    main()
