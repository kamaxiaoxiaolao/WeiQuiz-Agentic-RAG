"""Prepare SQuAD v1.1 data for WeiQuiz retrieval evaluation.

The script exports a small paragraph-level benchmark:

1. Text documents that can be ingested into the current RAG index.
2. squad_contexts.jsonl with stable paragraph metadata.
3. squad_queries.jsonl with query -> gold paragraph mappings.

It does not index anything by itself.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_OUTPUT_ROOT = Path("data/eval/squad")


@dataclass(frozen=True)
class SquadParagraph:
    doc_id: str
    paragraph_id: str
    title: str
    context: str


@dataclass(frozen=True)
class SquadQuery:
    query_id: str
    question: str
    gold_doc_id: str
    gold_paragraph_id: str
    gold_answer: str
    answer_start: int


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value.strip("_") or "doc"


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", "_", value.strip()) or "untitled"


def _load_squad_from_hf(split: str) -> list[dict]:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise RuntimeError("Missing dependency: datasets. Install it or pass --input-json.") from exc

    dataset = load_dataset("squad", split=split)
    return [dict(item) for item in dataset]


def _load_squad_from_json(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw

    if isinstance(raw, dict) and "data" in raw:
        rows: list[dict] = []
        for article in raw.get("data", []):
            title = str(article.get("title") or "")
            for paragraph in article.get("paragraphs", []):
                context = str(paragraph.get("context") or "")
                for qa in paragraph.get("qas", []):
                    answers = qa.get("answers") or []
                    rows.append(
                        {
                            "id": qa.get("id"),
                            "title": title,
                            "context": context,
                            "question": qa.get("question"),
                            "answers": {
                                "text": [a.get("text", "") for a in answers],
                                "answer_start": [a.get("answer_start", -1) for a in answers],
                            },
                        }
                    )
        return rows

    raise ValueError(f"Unsupported SQuAD JSON format: {path}")


def _answer_text(row: dict) -> str:
    answers = row.get("answers") or {}
    texts = answers.get("text") or []
    return str(texts[0]).strip() if texts else ""


def _answer_start(row: dict) -> int:
    answers = row.get("answers") or {}
    starts = answers.get("answer_start") or []
    try:
        return int(starts[0]) if starts else -1
    except (TypeError, ValueError):
        return -1


def _build_samples(rows: Sequence[dict], *, query_limit: int, seed: int) -> tuple[list[SquadParagraph], list[SquadQuery]]:
    candidates = [
        row
        for row in rows
        if str(row.get("context") or "").strip()
        and str(row.get("question") or "").strip()
        and _answer_text(row)
    ]

    rng = random.Random(seed)
    rng.shuffle(candidates)
    selected = candidates[:query_limit]

    paragraph_by_key: dict[tuple[str, str], SquadParagraph] = {}
    queries: list[SquadQuery] = []

    for row in selected:
        title = str(row.get("title") or "untitled").strip()
        context = str(row.get("context") or "").strip()
        key = (title, context)

        if key not in paragraph_by_key:
            paragraph_index = len(paragraph_by_key) + 1
            normalized_title = _normalize_title(title)
            doc_id = f"squad_doc_{paragraph_index:05d}"
            paragraph_id = f"squad_para_{paragraph_index:05d}"
            paragraph_by_key[key] = SquadParagraph(
                doc_id=doc_id,
                paragraph_id=paragraph_id,
                title=normalized_title,
                context=context,
            )

        paragraph = paragraph_by_key[key]
        raw_query_id = str(row.get("id") or f"q{len(queries) + 1:05d}")
        queries.append(
            SquadQuery(
                query_id=f"squad_{_safe_filename(raw_query_id)}",
                question=str(row.get("question") or "").strip(),
                gold_doc_id=paragraph.doc_id,
                gold_paragraph_id=paragraph.paragraph_id,
                gold_answer=_answer_text(row),
                answer_start=_answer_start(row),
            )
        )

    return list(paragraph_by_key.values()), queries


def _write_docs(paragraphs: Iterable[SquadParagraph], docs_dir: Path) -> int:
    docs_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for paragraph in paragraphs:
        content = "\n".join(
            [
                "---",
                f"doc_id: {paragraph.doc_id}",
                f"paragraph_id: {paragraph.paragraph_id}",
                "source: squad_v1",
                f"title: {paragraph.title}",
                "---",
                "",
                f"# {paragraph.title}",
                "",
                paragraph.context,
                "",
            ]
        )
        path = docs_dir / f"{_safe_filename(paragraph.doc_id)}.txt"
        path.write_text(content, encoding="utf-8")
        count += 1
    return count


def _write_contexts(paragraphs: Iterable[SquadParagraph], output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8") as f:
        for paragraph in paragraphs:
            row = {
                "doc_id": paragraph.doc_id,
                "paragraph_id": paragraph.paragraph_id,
                "title": paragraph.title,
                "source": "squad_v1",
                "text": paragraph.context,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _write_queries(queries: Iterable[SquadQuery], output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_path.open("w", encoding="utf-8") as f:
        for query in queries:
            row = {
                "id": query.query_id,
                "question": query.question,
                "gold_doc_id": query.gold_doc_id,
                "gold_paragraph_id": query.gold_paragraph_id,
                "gold_answer": query.gold_answer,
                "answer_start": query.answer_start,
                "question_type": "squad_paragraph_retrieval",
                "difficulty": "benchmark",
                "metadata": {
                    "source": "squad_v1",
                    "gold_paragraph_ids": [query.gold_paragraph_id],
                },
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def prepare_squad(
    *,
    output_root: Path,
    query_limit: int,
    seed: int,
    split: str,
    input_json: Path | None,
    clean: bool,
) -> None:
    if clean and output_root.exists():
        shutil.rmtree(output_root)

    rows = _load_squad_from_json(input_json) if input_json else _load_squad_from_hf(split)
    paragraphs, queries = _build_samples(rows, query_limit=query_limit, seed=seed)

    docs_dir = output_root / "docs"
    contexts_path = output_root / "squad_contexts.jsonl"
    queries_path = output_root / "squad_queries.jsonl"

    doc_count = _write_docs(paragraphs, docs_dir)
    context_count = _write_contexts(paragraphs, contexts_path)
    query_count = _write_queries(queries, queries_path)

    manifest = {
        "source": "squad_v1",
        "split": split,
        "input_json": str(input_json) if input_json else None,
        "output_root": str(output_root),
        "docs_dir": str(docs_dir),
        "contexts_path": str(contexts_path),
        "queries_path": str(queries_path),
        "query_limit": query_limit,
        "seed": seed,
        "exported_docs": doc_count,
        "exported_contexts": context_count,
        "exported_queries": query_count,
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("--- SQuAD Retrieval Eval Prepared ---")
    print(f"docs: {doc_count} -> {docs_dir}")
    print(f"contexts: {context_count} -> {contexts_path}")
    print(f"queries: {query_count} -> {queries_path}")
    print(f"manifest: {output_root / 'manifest.json'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert SQuAD v1.1 into WeiQuiz retrieval-eval files.")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Output benchmark root.")
    parser.add_argument("--queries", type=int, default=100, help="Number of SQuAD queries to export.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--split", default="validation", help="HuggingFace SQuAD split, usually validation or train.")
    parser.add_argument("--input-json", default=None, help="Optional local SQuAD v1.1 JSON file.")
    parser.add_argument("--clean", action="store_true", help="Delete output root before writing.")
    args = parser.parse_args()

    prepare_squad(
        output_root=Path(args.output_root),
        query_limit=args.queries,
        seed=args.seed,
        split=args.split,
        input_json=Path(args.input_json) if args.input_json else None,
        clean=args.clean,
    )


if __name__ == "__main__":
    main()
