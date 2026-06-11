"""Build index-aware node qrels for Chinese finance retrieval evaluation.

The stable golden set is anchored by doc/page/evidence text. This script maps
those evidence anchors to the current PostgreSQL chunk store, producing qrels
against real chunk ids. Regenerate this file whenever chunking or indexing is
rebuilt.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from llama_index.core.schema import TextNode

from app.config import settings as app_settings
from app.eval.eval_cn_finance_retrieval import evidence_overlap, normalize_path
from app.storage.parent_store import build_parent_store


DEFAULT_QUESTIONS = Path("data/eval_cn_finance/v2/golden_questions.jsonl")
DEFAULT_OUTPUT_DIR = Path("data/eval_cn_finance/v2")


def load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def source_path_of(node: TextNode) -> str:
    metadata = dict(node.metadata or {})
    return normalize_path(str(metadata.get("source_path") or metadata.get("parent_source_path") or ""))


def node_ids_for_qrels(node: TextNode) -> list[str]:
    metadata = dict(node.metadata or {})
    candidates = [
        node.node_id,
        metadata.get("chunk_id"),
        metadata.get("parent_id"),
    ]
    return list(dict.fromkeys(str(item) for item in candidates if item))


def build_nodes_by_source(nodes: list[TextNode]) -> dict[str, list[TextNode]]:
    grouped: dict[str, list[TextNode]] = defaultdict(list)
    for node in nodes:
        source = source_path_of(node)
        if source:
            grouped[source].append(node)
    return dict(grouped)


def match_context_to_nodes(
    *,
    context: dict[str, Any],
    nodes_by_source: dict[str, list[TextNode]],
    threshold: float,
    top_n: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    doc_id = normalize_path(str(context.get("doc_id") or ""))
    evidence_text = str(context.get("evidence_text") or "")
    candidates = nodes_by_source.get(doc_id, [])
    scored: list[tuple[float, TextNode]] = []
    for node in candidates:
        score = evidence_overlap(evidence_text, node.text or "")
        if score > 0:
            scored.append((score, node))
    scored.sort(key=lambda item: item[0], reverse=True)

    selected = [(score, node) for score, node in scored if score >= threshold][:top_n]
    if not selected and scored:
        selected = scored[:1]

    rows: list[dict[str, Any]] = []
    for selected_index, (score, node) in enumerate(selected):
        metadata = dict(node.metadata or {})
        qrel_role = "primary" if selected_index == 0 else "secondary"
        relevance = 2 if qrel_role == "primary" else 1
        for node_id in node_ids_for_qrels(node):
            rows.append(
                {
                    "evidence_id": context.get("evidence_id"),
                    "node_id": node_id,
                    "relevance": relevance,
                    "qrel_role": qrel_role,
                    "match_score": round(score, 4),
                    "node_source_path": metadata.get("source_path"),
                    "node_page_range": metadata.get("page_range"),
                    "node_chunk_role": metadata.get("chunk_role"),
                    "node_chunk_id": metadata.get("chunk_id") or node.node_id,
                    "node_parent_id": metadata.get("parent_id"),
                    "text_preview": (node.text or "")[:260],
                }
            )

    diagnostics = {
        "evidence_id": context.get("evidence_id"),
        "doc_id": doc_id,
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "best_score": round(scored[0][0], 4) if scored else 0.0,
        "used_fallback_best": bool(scored and not any(score >= threshold for score, _ in scored)),
    }
    return rows, diagnostics


def write_outputs(
    *,
    records: list[dict[str, Any]],
    nodes: list[TextNode],
    output_dir: Path,
    threshold: float,
    top_n: int,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    nodes_by_source = build_nodes_by_source(nodes)
    qrels_path = output_dir / "node_qrels.tsv"
    primary_qrels_path = output_dir / "primary_node_qrels.tsv"
    jsonl_path = output_dir / "node_qrels.jsonl"
    diagnostics_path = output_dir / "node_qrels_diagnostics.json"

    qrel_lines = ["query_id\tevidence_id\tnode_id\trelevance\tqrel_role\tmatch_score\n"]
    primary_qrel_lines = ["query_id\tevidence_id\tnode_id\trelevance\tqrel_role\tmatch_score\n"]
    json_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    for record in records:
        query_id = record["id"]
        for context in record.get("positive_contexts") or []:
            rows, diag = match_context_to_nodes(
                context=context,
                nodes_by_source=nodes_by_source,
                threshold=threshold,
                top_n=top_n,
            )
            diagnostics.append({"query_id": query_id, **diag})
            for row in rows:
                json_row = {"query_id": query_id, **row}
                json_rows.append(json_row)
                qrel_lines.append(
                    f"{query_id}\t{row['evidence_id']}\t{row['node_id']}\t{row['relevance']}\t{row['qrel_role']}\t{row['match_score']}\n"
                )
                if row["qrel_role"] == "primary":
                    primary_qrel_lines.append(
                        f"{query_id}\t{row['evidence_id']}\t{row['node_id']}\t{row['relevance']}\t{row['qrel_role']}\t{row['match_score']}\n"
                    )

    qrels_path.write_text("".join(qrel_lines), encoding="utf-8")
    primary_qrels_path.write_text("".join(primary_qrel_lines), encoding="utf-8")
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in json_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = {
        "node_count": len(nodes),
        "question_count": len(records),
        "qrel_row_count": len(json_rows),
        "threshold": threshold,
        "top_n": top_n,
        "unmapped_evidence_count": sum(1 for item in diagnostics if item["selected_count"] == 0),
        "fallback_best_count": sum(1 for item in diagnostics if item["used_fallback_best"]),
        "outputs": {
            "node_qrels_tsv": str(qrels_path),
            "primary_node_qrels_tsv": str(primary_qrels_path),
            "node_qrels_jsonl": str(jsonl_path),
            "diagnostics": str(diagnostics_path),
        },
    }
    diagnostics_path.write_text(
        json.dumps({"summary": summary, "details": diagnostics}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build node-level qrels from current indexed chunks.")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--threshold", type=float, default=0.25)
    parser.add_argument("--top-n", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = load_records(args.questions)
    store = build_parent_store(app_settings.postgres_url)
    nodes = store.list_chunk_nodes(leaf_only=True)
    summary = write_outputs(
        records=records,
        nodes=nodes,
        output_dir=args.output_dir,
        threshold=args.threshold,
        top_n=args.top_n,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
