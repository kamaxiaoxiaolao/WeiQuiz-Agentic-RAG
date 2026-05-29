"""Run an end-to-end RAGAS evaluation for WeiQuiz.

The script has two stages:
1. Build an evaluation dataset by asking the current RAG query engine.
2. Optionally run RAGAS metrics on the generated dataset.

RAGAS 0.4.x expects these columns:
- user_input
- response
- retrieved_contexts
- reference
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_INPUT = Path("data/eval/squad/squad_queries.jsonl")
DEFAULT_OUTPUT_DIR = Path("app/eval/ragas_runs")


def load_questions(path: Path, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if limit and len(rows) >= limit:
                break
    return rows


def build_eval_dataset(
    questions: list[dict[str, Any]],
    *,
    output_jsonl: Path,
    errors_jsonl: Path,
    max_errors: int,
) -> list[dict[str, Any]]:
    """Generate answers and retrieved contexts with the current RAG system."""

    from app.rag_milvus import build_rag_components

    _, _, _, query_engine = build_rag_components()
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    error_count = 0
    with output_jsonl.open("w", encoding="utf-8") as f, errors_jsonl.open("w", encoding="utf-8") as error_f:
        for idx, item in enumerate(questions, start=1):
            question = item["question"]
            print(f"[dataset] {idx}/{len(questions)} {question[:80]}")
            try:
                response = query_engine.query(question)
                source_nodes = getattr(response, "source_nodes", []) or []
                contexts = [_node_text(node) for node in source_nodes if _node_text(node)]
            except Exception as exc:
                error_count += 1
                error_record = {
                    "id": item.get("id") or f"sample_{idx}",
                    "user_input": question,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                error_f.write(json.dumps(error_record, ensure_ascii=False) + "\n")
                print(f"[dataset:error] {idx}/{len(questions)} {type(exc).__name__}: {exc}")
                if max_errors >= 0 and error_count > max_errors:
                    raise RuntimeError(
                        f"Too many dataset generation errors: {error_count} > {max_errors}. "
                        f"See {errors_jsonl}"
                    ) from exc
                continue

            record = {
                "id": item.get("id") or f"sample_{idx}",
                "user_input": question,
                "response": str(response),
                "retrieved_contexts": contexts,
                "reference": item.get("gold_answer") or "",
                "gold_doc_id": item.get("gold_doc_id"),
                "gold_paragraph_id": item.get("gold_paragraph_id"),
                "source_count": len(contexts),
            }
            records.append(record)
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return records


def load_eval_dataset(path: Path, limit: int = 0) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            records.append(json.loads(line))
            if limit and len(records) >= limit:
                break
    return records


def run_ragas(records: list[dict[str, Any]], *, metrics: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from datasets import Dataset
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    from ragas import evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

    from app.config import settings

    dataset = Dataset.from_list(
        [
            {
                "user_input": r["user_input"],
                "response": r["response"],
                "retrieved_contexts": r["retrieved_contexts"],
                "reference": r["reference"],
            }
            for r in records
        ]
    )

    evaluator_llm = LangchainLLMWrapper(
        ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base,
            temperature=0,
        )
    )
    evaluator_embeddings = LangchainEmbeddingsWrapper(
        OpenAIEmbeddings(
            model=settings.embedding_model,
            api_key=settings.qwen_llm_api_key,
            base_url=settings.embedding_api_base,
        )
    )

    metric_map = {
        "faithfulness": faithfulness,
        "answer_relevancy": answer_relevancy,
        "context_precision": context_precision,
        "context_recall": context_recall,
    }
    selected_metrics = []
    for name in metrics:
        if name not in metric_map:
            raise ValueError(f"Unsupported metric: {name}")
        selected_metrics.append(metric_map[name])

    result = evaluate(
        dataset,
        metrics=selected_metrics,
        llm=evaluator_llm,
        embeddings=evaluator_embeddings,
        raise_exceptions=False,
    )
    frame = result.to_pandas()
    details = json.loads(frame.to_json(orient="records", force_ascii=False))
    summary = _summarize_scores(details, metrics)
    return summary, details


def write_outputs(
    *,
    output_dir: Path,
    dataset_path: Path,
    summary: dict[str, Any] | None,
    details: list[dict[str, Any]] | None,
    metadata: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    if summary is not None:
        summary_path = output_dir / "ragas_scores.json"
        summary_path.write_text(
            json.dumps({"metadata": metadata, "summary": summary, "details": details}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    report_path = output_dir / "ragas_report.md"
    report_path.write_text(
        _build_report(dataset_path=dataset_path, summary=summary, details=details, metadata=metadata),
        encoding="utf-8",
    )


def _summarize_scores(details: list[dict[str, Any]], metrics: list[str]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for name in metrics:
        values = []
        for row in details:
            value = row.get(name)
            if isinstance(value, (int, float)):
                values.append(float(value))
        summary[name] = round(mean(values), 4) if values else None
    return summary


def _build_report(
    *,
    dataset_path: Path,
    summary: dict[str, Any] | None,
    details: list[dict[str, Any]] | None,
    metadata: dict[str, Any],
) -> str:
    lines = [
        "# RAGAS Evaluation Report",
        "",
        f"- Time: {metadata['created_at']}",
        f"- Dataset: `{dataset_path}`",
        f"- Samples: {metadata['sample_count']}",
        f"- Errors: {metadata.get('error_count', 0)}",
        f"- Mode: {metadata['mode']}",
        "",
    ]

    if summary is None:
        lines.extend(
            [
                "## Status",
                "",
                "Dataset prepared only. RAGAS metrics were not executed.",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(["## Summary", "", "| Metric | Score |", "|---|---:|"])
    for key, value in summary.items():
        score = "-" if value is None else f"{value:.4f}"
        lines.append(f"| {key} | {score} |")

    if details:
        lines.extend(["", "## Lowest Faithfulness Samples", ""])
        ranked = sorted(
            details,
            key=lambda row: row.get("faithfulness") if isinstance(row.get("faithfulness"), (int, float)) else 999,
        )
        for row in ranked[:5]:
            question = str(row.get("user_input", ""))[:120].replace("\n", " ")
            score = row.get("faithfulness", "-")
            lines.append(f"- faithfulness={score} | {question}")

    return "\n".join(lines) + "\n"


def _node_text(node: object) -> str:
    if hasattr(node, "text"):
        return str(getattr(node, "text") or "")
    inner = getattr(node, "node", None)
    if inner is not None and hasattr(inner, "text"):
        return str(getattr(inner, "text") or "")
    get_content = getattr(node, "get_content", None)
    if callable(get_content):
        return str(get_content() or "")
    return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run WeiQuiz RAGAS evaluation.")
    parser.add_argument("--input-jsonl", type=Path, default=DEFAULT_INPUT, help="Question JSONL file.")
    parser.add_argument("--dataset-jsonl", type=Path, default=None, help="Precomputed RAGAS dataset JSONL.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory.")
    parser.add_argument("--limit", type=int, default=10, help="Number of samples to evaluate.")
    parser.add_argument(
        "--max-errors",
        type=int,
        default=3,
        help="Maximum allowed dataset generation errors. Use -1 to never fail on sample errors.",
    )
    parser.add_argument("--prepare-only", action="store_true", help="Only generate the RAGAS dataset.")
    parser.add_argument(
        "--metrics",
        default="faithfulness,answer_relevancy,context_precision,context_recall",
        help="Comma-separated RAGAS metrics.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = args.output_dir / run_id
    dataset_path = args.dataset_jsonl or (output_dir / "ragas_dataset.jsonl")
    errors_path = output_dir / "ragas_dataset_errors.jsonl"

    if args.dataset_jsonl:
        records = load_eval_dataset(args.dataset_jsonl, limit=args.limit)
        error_count = 0
    else:
        questions = load_questions(args.input_jsonl, limit=args.limit)
        records = build_eval_dataset(
            questions,
            output_jsonl=dataset_path,
            errors_jsonl=errors_path,
            max_errors=args.max_errors,
        )
        error_count = max(len(questions) - len(records), 0)

    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sample_count": len(records),
        "error_count": error_count,
        "errors_path": str(errors_path) if not args.dataset_jsonl else "",
        "mode": "prepare_only" if args.prepare_only else "evaluate",
        "metrics": [m.strip() for m in args.metrics.split(",") if m.strip()],
    }

    if args.prepare_only:
        write_outputs(
            output_dir=output_dir,
            dataset_path=dataset_path,
            summary=None,
            details=None,
            metadata=metadata,
        )
        print(f"RAGAS dataset written to: {dataset_path}")
        print(f"Report written to: {output_dir / 'ragas_report.md'}")
        return

    summary, details = run_ragas(records, metrics=metadata["metrics"])
    write_outputs(
        output_dir=output_dir,
        dataset_path=dataset_path,
        summary=summary,
        details=details,
        metadata=metadata,
    )
    print(f"Scores written to: {output_dir / 'ragas_scores.json'}")
    print(f"Report written to: {output_dir / 'ragas_report.md'}")


if __name__ == "__main__":
    main()
