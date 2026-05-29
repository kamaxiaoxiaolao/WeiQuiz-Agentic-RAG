"""Prepare BEIR SciFact data for the WeiQuiz ingestion/evaluation pipeline.

The script converts a sampled BEIR SciFact benchmark split into:

1. Plain text documents that can be ingested by app.ingest.milvus_loader.
2. A retrieval_questions.jsonl file compatible with app.eval.retrieval_eval.

It does not index anything by itself.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Set


DEFAULT_BEIR_ROOT = Path("data/beir/scifact")
DEFAULT_OUTPUT_ROOT = Path("data/benchmarks/beir_scifact")


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("_") or "doc"


def _load_jsonl(path: Path) -> Dict[str, dict]:
    items: Dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            item_id = str(data.get("_id") or data.get("id"))
            items[item_id] = data
    return items


def _load_queries(path: Path) -> Dict[str, str]:
    raw = _load_jsonl(path)
    return {query_id: str(item.get("text") or item.get("query") or "") for query_id, item in raw.items()}


def _load_qrels(path: Path) -> Dict[str, Dict[str, int]]:
    qrels: Dict[str, Dict[str, int]] = {}
    with path.open("r", encoding="utf-8") as f:
        header = f.readline().strip().split("\t")
        query_idx = header.index("query-id") if "query-id" in header else 0
        corpus_idx = header.index("corpus-id") if "corpus-id" in header else 1
        score_idx = header.index("score") if "score" in header else 2

        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) <= max(query_idx, corpus_idx, score_idx):
                continue
            query_id = parts[query_idx]
            corpus_id = parts[corpus_idx]
            score = int(float(parts[score_idx]))
            if score <= 0:
                continue
            qrels.setdefault(query_id, {})[corpus_id] = score
    return qrels


def _keywords_from_query(query: str, *, limit: int = 5) -> List[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]+", query)
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "show",
        "shows",
        "are",
        "were",
        "was",
        "has",
        "have",
        "from",
        "into",
    }
    keywords: List[str] = []
    for word in words:
        lower = word.lower()
        if lower in stop or len(lower) < 4:
            continue
        if word not in keywords:
            keywords.append(word)
        if len(keywords) >= limit:
            break
    return keywords or words[:limit]


def _select_queries(qrels: Dict[str, Dict[str, int]], *, limit: int, seed: int) -> List[str]:
    query_ids = sorted(query_id for query_id, docs in qrels.items() if docs)
    rng = random.Random(seed)
    rng.shuffle(query_ids)
    return query_ids[:limit]


def _collect_doc_ids(
    selected_query_ids: Iterable[str],
    qrels: Dict[str, Dict[str, int]],
    corpus: Dict[str, dict],
    *,
    negative_docs: int,
    seed: int,
) -> Set[str]:
    doc_ids: Set[str] = set()
    for query_id in selected_query_ids:
        doc_ids.update(qrels.get(query_id, {}).keys())

    available_negatives = sorted(set(corpus) - doc_ids)
    rng = random.Random(seed)
    rng.shuffle(available_negatives)
    doc_ids.update(available_negatives[:negative_docs])
    return doc_ids


def _write_docs(corpus: Dict[str, dict], doc_ids: Iterable[str], docs_dir: Path) -> int:
    docs_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for corpus_id in sorted(doc_ids):
        item = corpus.get(corpus_id)
        if not item:
            continue

        doc_id = f"beir_scifact_{corpus_id}"
        title = str(item.get("title") or "").strip()
        text = str(item.get("text") or "").strip()
        content = "\n".join(
            [
                "---",
                f"doc_id: {doc_id}",
                "source: beir_scifact",
                f"beir_corpus_id: {corpus_id}",
                f"title: {title}",
                "---",
                "",
                f"# {title}" if title else "",
                "",
                text,
                "",
            ]
        ).strip() + "\n"

        path = docs_dir / f"{_safe_filename(doc_id)}.txt"
        path.write_text(content, encoding="utf-8")
        count += 1
    return count


def _write_questions(
    selected_query_ids: Iterable[str],
    queries: Dict[str, str],
    qrels: Dict[str, Dict[str, int]],
    output_path: Path,
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8") as f:
        for query_id in selected_query_ids:
            query = queries.get(query_id, "").strip()
            gold_docs = sorted(qrels.get(query_id, {}))
            if not query or not gold_docs:
                continue

            gold_corpus_id = gold_docs[0]
            row = {
                "id": f"beir_scifact_{query_id}",
                "question": query,
                "gold_doc_id": f"beir_scifact_{gold_corpus_id}",
                "gold_keywords": _keywords_from_query(query),
                "reference_answer": "",
                "question_type": "beir_scifact",
                "difficulty": "benchmark",
                "metadata": {
                    "beir_query_id": query_id,
                    "beir_gold_doc_ids": [f"beir_scifact_{doc_id}" for doc_id in gold_docs],
                },
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def prepare_scifact(
    *,
    beir_root: Path,
    output_root: Path,
    query_limit: int,
    negative_docs: int,
    seed: int,
    clean: bool,
) -> None:
    corpus_path = beir_root / "corpus.jsonl"
    queries_path = beir_root / "queries.jsonl"
    qrels_path = beir_root / "qrels" / "test.tsv"

    for path in (corpus_path, queries_path, qrels_path):
        if not path.exists():
            raise FileNotFoundError(f"Missing BEIR SciFact file: {path}")

    if clean and output_root.exists():
        shutil.rmtree(output_root)

    docs_dir = output_root / "docs"
    eval_path = output_root / "retrieval_questions.jsonl"

    corpus = _load_jsonl(corpus_path)
    queries = _load_queries(queries_path)
    qrels = _load_qrels(qrels_path)

    selected_query_ids = _select_queries(qrels, limit=query_limit, seed=seed)
    doc_ids = _collect_doc_ids(
        selected_query_ids,
        qrels,
        corpus,
        negative_docs=negative_docs,
        seed=seed,
    )

    doc_count = _write_docs(corpus, doc_ids, docs_dir)
    question_count = _write_questions(selected_query_ids, queries, qrels, eval_path)

    manifest = {
        "source": "beir_scifact",
        "beir_root": str(beir_root),
        "output_root": str(output_root),
        "docs_dir": str(docs_dir),
        "eval_path": str(eval_path),
        "query_limit": query_limit,
        "negative_docs": negative_docs,
        "seed": seed,
        "exported_docs": doc_count,
        "exported_questions": question_count,
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("--- BEIR SciFact Prepared ---")
    print(f"docs: {doc_count} -> {docs_dir}")
    print(f"questions: {question_count} -> {eval_path}")
    print(f"manifest: {output_root / 'manifest.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert BEIR SciFact into WeiQuiz docs/eval JSONL.")
    parser.add_argument("--beir-root", default=str(DEFAULT_BEIR_ROOT), help="Path containing corpus.jsonl, queries.jsonl and qrels/test.tsv.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Output benchmark root.")
    parser.add_argument("--queries", type=int, default=50, help="Number of test queries to export.")
    parser.add_argument("--negative-docs", type=int, default=100, help="Additional irrelevant documents to export as distractors.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--clean", action="store_true", help="Delete output root before writing.")
    args = parser.parse_args()

    prepare_scifact(
        beir_root=Path(args.beir_root),
        output_root=Path(args.output_root),
        query_limit=args.queries,
        negative_docs=args.negative_docs,
        seed=args.seed,
        clean=args.clean,
    )


if __name__ == "__main__":
    main()
