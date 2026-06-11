from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_QUESTIONS = Path("data/eval_cn_finance/v3/golden_questions.jsonl")
DEFAULT_OUTPUT = Path("data/eval_cn_finance/v3/quality_report.md")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _table(counter: Counter[str]) -> list[str]:
    lines = ["| Item | Count |", "|---|---:|"]
    lines.extend(f"| {key} | {value} |" for key, value in counter.most_common())
    return lines


def audit(records: list[dict[str, Any]]) -> str:
    questions = [str(record.get("question") or "") for record in records]
    duplicate_questions = [question for question, count in Counter(questions).items() if count > 1]
    missing_positive = [record["id"] for record in records if not record.get("positive_contexts")]
    multi_evidence = [record for record in records if len(record.get("positive_contexts") or []) > 1]
    abstentions = [record for record in records if record.get("eval_type") == "abstention"]
    bad_scope = [
        record["id"]
        for record in records
        if record.get("question_type") in {"table_numeric", "hard_negative_table_numeric"}
        and ("单位" not in str(record.get("question") or ""))
    ]

    lines = [
        "# Chinese Finance Eval Quality Report",
        "",
        f"- Questions: {len(records)}",
        f"- Unique questions: {len(set(questions))}",
        f"- Duplicate questions: {len(duplicate_questions)}",
        f"- Multi-evidence questions: {len(multi_evidence)}",
        f"- Abstention questions: {len(abstentions)}",
        f"- Missing positive contexts: {len(missing_positive)}",
        f"- Numeric questions without explicit unit scope: {len(bad_scope)}",
        "",
        "## By Question Type",
        "",
        *_table(Counter(str(record.get("question_type") or "unknown") for record in records)),
        "",
        "## By Difficulty",
        "",
        *_table(Counter(str(record.get("difficulty") or "unknown") for record in records)),
        "",
        "## Review Notes",
        "",
        "- Evidence anchors are stable source_path/page/evidence_text, not chunk ids.",
        "- Regenerate node qrels after every chunk or index rebuild.",
        "- Use primary_node_qrels.tsv for strict core-evidence metrics.",
        "- Use node_qrels.tsv for wider relevant-context metrics.",
    ]

    if duplicate_questions:
        lines.extend(["", "## Duplicate Questions", ""])
        lines.extend(f"- {question}" for question in duplicate_questions[:30])
    if missing_positive:
        lines.extend(["", "## Missing Positive Contexts", ""])
        lines.extend(f"- {question_id}" for question_id in missing_positive[:30])
    if bad_scope:
        lines.extend(["", "## Numeric Scope Warnings", ""])
        lines.extend(f"- {question_id}" for question_id in bad_scope[:30])

    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit Chinese finance eval set quality.")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = load_jsonl(args.questions)
    report = audit(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
