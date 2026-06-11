from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path("data/eval_cn_finance/v2/golden_questions.jsonl")
DEFAULT_OUTPUT_DIR = Path("data/eval_cn_finance/v3")

METRIC_LABELS = {
    "revenue": "营业收入",
    "net_profit": "归属于上市公司股东的净利润",
    "cashflow": "经营活动产生的现金流量净额",
    "total_assets": "总资产",
    "eps": "基本每股收益",
}

PROFILE_LABELS = {
    "legal_rep": "法定代表人",
    "website": "官方网站",
    "registered_address": "注册地址",
    "stock_code": "A股股票代码",
}

STOCK_NAMES = {
    "000333": "美的集团",
    "000651": "格力电器",
    "000858": "五粮液",
    "002415": "海康威视",
    "002594": "比亚迪",
    "300750": "宁德时代",
    "600036": "招商银行",
    "600519": "贵州茅台",
    "601012": "隆基绿能",
    "601318": "中国平安",
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _company(record: dict[str, Any]) -> str:
    return str(record.get("company") or "").strip() or "该公司"


def _metric(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") or {}
    return METRIC_LABELS.get(str(metadata.get("metric") or ""), "相关指标")


def _profile_field(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") or {}
    return PROFILE_LABELS.get(str(metadata.get("field") or ""), "公司基本信息")


def _companies_from_gold_files(record: dict[str, Any]) -> list[str]:
    companies: list[str] = []
    for gold_file in record.get("gold_files") or []:
        ticker = str(gold_file).split("/", 1)[0]
        company = STOCK_NAMES.get(ticker)
        if company and company not in companies:
            companies.append(company)
    return companies


def rewrite_question(record: dict[str, Any]) -> str:
    company = _company(record)
    question_type = str(record.get("question_type") or "")

    if question_type == "company_profile":
        return f"根据{company}2025年年度报告，公司的{_profile_field(record)}是什么？"

    if question_type == "table_numeric":
        return f"根据{company}2025年年度报告，{_metric(record)}的披露数值是多少？请同时保留报告单位。"

    if question_type == "quarterly_table_numeric":
        return f"根据{company}2026年第一季度报告，{_metric(record)}是多少？请回答披露数值和单位。"

    if question_type == "cross_year_compare":
        return f"{company}2025年和2024年的{_metric(record)}分别是多少？报告披露的同比变化如何？"

    if question_type == "cross_doc_multi_hop":
        return f"综合{company}2025年年度报告和2026年第一季度报告，全年与一季度的营业收入分别是多少？"

    if question_type == "cross_company_compare":
        companies = _companies_from_gold_files(record)
        if len(companies) >= 2:
            left, right = companies[:2]
            return f"请分别查找{left}和{right}2025年年度报告披露的营业收入，说明各自单位，并判断是否可以直接比较。"
        return record["question"]

    if question_type == "unanswerable":
        return f"只依据{company}2025年年度报告，能否找到2027年净利润预测的具体金额？如果没有披露，请明确说明。"

    if question_type == "hard_negative_table_numeric":
        return f"只依据{company}2025年年度报告正文，找出2025年的{_metric(record)}，保留报告披露单位，不要使用一季报或其他公告中的数字。"

    return str(record.get("question") or "").strip()


def refine_record(record: dict[str, Any], index: int) -> dict[str, Any]:
    refined = dict(record)
    refined["id"] = f"cn_fin_v3_{index:03d}"
    refined["source_eval_id"] = record.get("id")
    refined["question"] = rewrite_question(record)
    metadata = dict(refined.get("metadata") or {})
    metadata["refined_from"] = record.get("id")
    metadata["question_refinement"] = "more_natural_and_more_explicit_scope"
    refined["metadata"] = metadata
    return refined


def write_outputs(records: list[dict[str, Any]], output_dir: Path, input_path: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    golden_path = output_dir / "golden_questions.jsonl"
    queries_path = output_dir / "queries.jsonl"
    qrels_path = output_dir / "qrels.tsv"
    evidence_path = output_dir / "evidence_catalog.jsonl"
    manifest_path = output_dir / "manifest.json"

    with golden_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    with queries_path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(
                json.dumps(
                    {
                        "query_id": record["id"],
                        "question": record["question"],
                        "difficulty": record.get("difficulty"),
                        "question_type": record.get("question_type"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    seen_evidence: dict[str, dict[str, Any]] = {}
    with qrels_path.open("w", encoding="utf-8") as f:
        f.write("query_id\tevidence_id\trelevance\n")
        for record in records:
            for context in record.get("positive_contexts") or []:
                f.write(f"{record['id']}\t{context['evidence_id']}\t{context.get('relevance', 1)}\n")
                seen_evidence.setdefault(context["evidence_id"], context)

    with evidence_path.open("w", encoding="utf-8") as f:
        for evidence in seen_evidence.values():
            f.write(json.dumps(evidence, ensure_ascii=False) + "\n")

    manifest = {
        "version": "v3",
        "source": str(input_path),
        "count": len(records),
        "outputs": {
            "golden_questions": str(golden_path),
            "queries": str(queries_path),
            "qrels": str(qrels_path),
            "evidence_catalog": str(evidence_path),
        },
        "by_difficulty": dict(Counter(str(record.get("difficulty") or "unknown") for record in records)),
        "by_question_type": dict(Counter(str(record.get("question_type") or "unknown") for record in records)),
        "multi_evidence_count": sum(1 for record in records if len(record.get("positive_contexts") or []) > 1),
        "abstention_count": sum(1 for record in records if record.get("eval_type") == "abstention"),
        "notes": [
            "Questions are refined from v2 for clearer scope and more natural user wording.",
            "Evidence anchors remain stable: source_path, page, and evidence_text are unchanged.",
            "Regenerate node qrels after every chunking/index rebuild.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refine Chinese finance eval questions without changing evidence anchors.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = load_jsonl(args.input)
    refined = [refine_record(record, index) for index, record in enumerate(records, start=1)]
    write_outputs(refined, args.output_dir, args.input)


if __name__ == "__main__":
    main()
