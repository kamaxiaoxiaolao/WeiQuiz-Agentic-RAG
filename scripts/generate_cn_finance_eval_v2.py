from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate_cn_finance_golden_eval import (
    COMPANY_NAMES,
    DocInfo,
    choose_annuals,
    choose_q1_reports,
    compact_evidence,
    detect_unit,
    extract_after_label,
    find_page,
    load_meta_docs,
    numeric_values_after,
    page_text,
    split_lines,
)


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    doc: DocInfo
    page: int
    text: str
    label: str


def to_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").replace("%", ""))
    except (InvalidOperation, ValueError):
        return None


def evidence_id(doc: DocInfo, page: int, label: str) -> str:
    safe_label = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", label).strip("_").lower()
    return f"{doc.rel_path}#page={page}#span={safe_label or 'evidence'}"


def snippet_around_label(lines: list[str], labels: Iterable[str], *, window: int = 10) -> str:
    labels = list(labels)
    for index, line in enumerate(lines):
        if any(label in line for label in labels):
            start = max(0, index - 2)
            end = min(len(lines), index + window)
            return "\n".join(lines[start:end]).strip()
    return "\n".join(lines[:window]).strip()


def metric_record(lines: list[str], labels: list[str], *, scan: int = 18) -> dict[str, Any] | None:
    values = numeric_values_after(lines, labels, scan=scan)
    if not values:
        return None
    return {
        "values": values,
        "snippet": snippet_around_label(lines, labels, window=12),
    }


def extract_annual_data(doc: DocInfo) -> dict[str, Any] | None:
    with fitz.open(str(doc.path)) as pdf:
        best: tuple[int, str] | None = None
        for idx in range(min(pdf.page_count, 70)):
            text = page_text(pdf, idx + 1)
            if ("营业收入" in text or "营业总收入" in text) and (
                "归属于上市公司股东" in text or "归属于母公司股东" in text
            ):
                best = (idx + 1, text)
                break
        if not best:
            return None

    page_no, text = best
    lines = split_lines(text)
    metrics = {
        "revenue": metric_record(lines, ["营业收入", "营业总收入"]),
        "net_profit": metric_record(
            lines,
            [
                "归属于上市公司股东的净利润",
                "归属于母公司股东的净利润",
                "归属于上市公司股东净利润",
            ],
        ),
        "cashflow": metric_record(lines, ["经营活动产生的现金流量净额"]),
        "total_assets": metric_record(lines, ["总资产"]),
        "eps": metric_record(lines, ["基本每股收益"]),
    }
    metrics = {key: value for key, value in metrics.items() if value}
    if not metrics.get("revenue"):
        return None

    evidence_text = compact_evidence(text, 1200)
    return {
        "page": page_no,
        "unit": detect_unit(text),
        "evidence": Evidence(
            evidence_id=evidence_id(doc, page_no, "annual_main_metrics"),
            doc=doc,
            page=page_no,
            text=evidence_text,
            label="annual_main_metrics",
        ),
        "metrics": metrics,
    }


def extract_q1_data(doc: DocInfo) -> dict[str, Any] | None:
    with fitz.open(str(doc.path)) as pdf:
        best: tuple[int, str] | None = None
        for idx in range(min(pdf.page_count, 25)):
            text = page_text(pdf, idx + 1)
            if ("营业收入" in text or "营业总收入" in text) and (
                "归属于上市公司股东" in text or "归属于母公司股东" in text or "净利润" in text
            ):
                best = (idx + 1, text)
                break
        if not best:
            return None

    page_no, text = best
    lines = split_lines(text)
    metrics = {
        "revenue": metric_record(lines, ["营业收入", "营业总收入"]),
        "net_profit": metric_record(
            lines,
            [
                "归属于上市公司股东的净利润",
                "归属于母公司股东的净利润",
                "净利润",
            ],
        ),
    }
    metrics = {key: value for key, value in metrics.items() if value}
    if not metrics.get("revenue"):
        return None
    return {
        "page": page_no,
        "unit": detect_unit(text),
        "evidence": Evidence(
            evidence_id=evidence_id(doc, page_no, "q1_main_metrics"),
            doc=doc,
            page=page_no,
            text=compact_evidence(text, 1000),
            label="q1_main_metrics",
        ),
        "metrics": metrics,
    }


def extract_company_info(doc: DocInfo) -> dict[str, Any] | None:
    with fitz.open(str(doc.path)) as pdf:
        result = find_page(pdf, ["公司的法定代表人"], max_pages=25)
    if not result:
        return None
    page_no, text = result
    lines = split_lines(text)
    values = {
        "legal_rep": extract_after_label(lines, "公司的法定代表人"),
        "website": extract_after_label(lines, "公司网址"),
        "registered_address": extract_after_label(lines, "注册地址") or extract_after_label(lines, "公司注册地址"),
        "stock_code": doc.ticker,
    }
    values = {key: value for key, value in values.items() if value}
    if not values:
        return None
    return {
        "page": page_no,
        "values": values,
        "evidence": Evidence(
            evidence_id=evidence_id(doc, page_no, "company_profile"),
            doc=doc,
            page=page_no,
            text=compact_evidence(text, 1000),
            label="company_profile",
        ),
    }


def add_record(
    records: list[dict[str, Any]],
    *,
    question: str,
    answer: str,
    evidences: list[Evidence],
    difficulty: str,
    question_type: str,
    company: str | None = None,
    stock_code: str | None = None,
    eval_type: str = "exact_or_semantic",
    metadata: dict[str, Any] | None = None,
) -> None:
    idx = len(records) + 1
    primary = evidences[0] if evidences else None
    records.append(
        {
            "id": f"cn_fin_v2_{idx:03d}",
            "question": question,
            "answer": answer,
            "company": company if company is not None else (primary.doc.company if primary else None),
            "stock_code": stock_code if stock_code is not None else (primary.doc.ticker if primary else None),
            "doc_type": primary.doc.document_type if primary else None,
            "difficulty": difficulty,
            "question_type": question_type,
            "gold_files": [ev.doc.rel_path for ev in evidences],
            "gold_pages": [ev.page for ev in evidences],
            "gold_evidence": "\n\n".join(ev.text for ev in evidences),
            "positive_contexts": [
                {
                    "evidence_id": ev.evidence_id,
                    "doc_id": ev.doc.rel_path,
                    "page": ev.page,
                    "evidence_text": ev.text,
                    "label": ev.label,
                    "relevance": 1,
                }
                for ev in evidences
            ],
            "eval_type": eval_type,
            "metadata": metadata or {},
        }
    )


def metric_value(data: dict[str, Any], metric: str, index: int = 0) -> str | None:
    item = data["metrics"].get(metric)
    if not item:
        return None
    values = item.get("values") or []
    if len(values) <= index:
        return None
    return str(values[index])


def metric_snippet_evidence(doc: DocInfo, data: dict[str, Any], metric: str) -> Evidence:
    item = data["metrics"].get(metric) or {}
    text = item.get("snippet") or data["evidence"].text
    return Evidence(
        evidence_id=evidence_id(doc, int(data["page"]), f"annual_{metric}"),
        doc=doc,
        page=int(data["page"]),
        text=compact_evidence(text, 700),
        label=f"annual_{metric}",
    )


def q1_metric_snippet_evidence(doc: DocInfo, data: dict[str, Any], metric: str) -> Evidence:
    item = data["metrics"].get(metric) or {}
    text = item.get("snippet") or data["evidence"].text
    return Evidence(
        evidence_id=evidence_id(doc, int(data["page"]), f"q1_{metric}"),
        doc=doc,
        page=int(data["page"]),
        text=compact_evidence(text, 700),
        label=f"q1_{metric}",
    )


def build_records(root: Path, target_count: int) -> list[dict[str, Any]]:
    docs = load_meta_docs(root)
    annuals = choose_annuals(docs)
    q1s = choose_q1_reports(docs)
    annual_data = {ticker: extract_annual_data(doc) for ticker, doc in annuals.items()}
    annual_data = {ticker: data for ticker, data in annual_data.items() if data}
    q1_data = {ticker: extract_q1_data(doc) for ticker, doc in q1s.items()}
    q1_data = {ticker: data for ticker, data in q1_data.items() if data}
    info_data = {ticker: extract_company_info(doc) for ticker, doc in annuals.items()}
    info_data = {ticker: data for ticker, data in info_data.items() if data}

    records: list[dict[str, Any]] = []

    profile_templates = {
        "legal_rep": ("{company}2025年年度报告披露的法定代表人是谁？", "{company}法定代表人为{value}。"),
        "website": ("{company}2025年年度报告披露的公司网址是什么？", "{company}公司网址为{value}。"),
        "registered_address": ("{company}2025年年度报告披露的注册地址是什么？", "{company}注册地址为{value}。"),
        "stock_code": ("{company}2025年年度报告对应的A股股票代码是什么？", "{company}A股股票代码为{value}。"),
    }
    for ticker in sorted(info_data):
        doc = annuals[ticker]
        info = info_data[ticker]
        for key, (question_tmpl, answer_tmpl) in profile_templates.items():
            value = info["values"].get(key)
            if not value:
                continue
            add_record(
                records,
                question=question_tmpl.format(company=doc.company),
                answer=answer_tmpl.format(company=doc.company, value=value),
                evidences=[info["evidence"]],
                difficulty="simple",
                question_type="company_profile",
                metadata={"field": key},
            )

    annual_metric_templates = {
        "revenue": ("营业收入", "annual_revenue"),
        "net_profit": ("归属于上市公司股东的净利润", "annual_net_profit"),
        "cashflow": ("经营活动产生的现金流量净额", "annual_cashflow"),
        "total_assets": ("总资产", "annual_total_assets"),
        "eps": ("基本每股收益", "annual_eps"),
    }
    for ticker in sorted(annual_data):
        doc = annuals[ticker]
        data = annual_data[ticker]
        for metric, (label, qtype) in annual_metric_templates.items():
            value = metric_value(data, metric, 0)
            if value is None:
                continue
            add_record(
                records,
                question=f"{doc.company}2025年年度报告披露的{label}是多少？",
                answer=f"{doc.company}2025年{label}为{value}，单位：{data['unit']}。",
                evidences=[metric_snippet_evidence(doc, data, metric)],
                difficulty="simple" if metric in {"revenue", "net_profit"} else "medium",
                question_type="table_numeric",
                metadata={"metric": metric, "year": "2025"},
            )

    for ticker in sorted(q1_data):
        doc = q1s[ticker]
        data = q1_data[ticker]
        for metric, label in [("revenue", "营业收入"), ("net_profit", "归属于上市公司股东的净利润")]:
            value = metric_value(data, metric, 0)
            if value is None:
                continue
            add_record(
                records,
                question=f"{doc.company}2026年第一季度报告披露的{label}是多少？",
                answer=f"{doc.company}2026年第一季度{label}为{value}，单位：{data['unit']}。",
                evidences=[q1_metric_snippet_evidence(doc, data, metric)],
                difficulty="medium",
                question_type="quarterly_table_numeric",
                metadata={"metric": metric, "period": "2026Q1"},
            )

    for ticker in sorted(annual_data):
        doc = annuals[ticker]
        data = annual_data[ticker]
        for metric, label in [("revenue", "营业收入"), ("net_profit", "归属于上市公司股东的净利润")]:
            value_2025 = metric_value(data, metric, 0)
            value_2024 = metric_value(data, metric, 1)
            change = metric_value(data, metric, 2)
            if value_2025 is None or value_2024 is None:
                continue
            answer = f"{doc.company}2025年{label}为{value_2025}，2024年为{value_2024}，单位：{data['unit']}。"
            if change:
                answer += f"报告披露的同比变化为{change}。"
            add_record(
                records,
                question=f"{doc.company}2025年{label}相比2024年如何变化？请给出两年数值。",
                answer=answer,
                evidences=[metric_snippet_evidence(doc, data, metric)],
                difficulty="complex",
                question_type="cross_year_compare",
                metadata={"metric": metric, "years": ["2025", "2024"]},
            )

    for ticker in sorted(set(annual_data) & set(q1_data)):
        annual_doc = annuals[ticker]
        q1_doc = q1s[ticker]
        annual = annual_data[ticker]
        q1 = q1_data[ticker]
        annual_value = metric_value(annual, "revenue", 0)
        q1_value = metric_value(q1, "revenue", 0)
        if annual_value is None or q1_value is None:
            continue
        add_record(
            records,
            question=f"{annual_doc.company}2025年全年营业收入和2026年第一季度营业收入分别是多少？",
            answer=(
                f"{annual_doc.company}2025年全年营业收入为{annual_value}，单位：{annual['unit']}；"
                f"2026年第一季度营业收入为{q1_value}，单位：{q1['unit']}。"
            ),
            evidences=[
                metric_snippet_evidence(annual_doc, annual, "revenue"),
                q1_metric_snippet_evidence(q1_doc, q1, "revenue"),
            ],
            difficulty="multi_hop",
            question_type="cross_doc_multi_hop",
            metadata={"requires_all_evidence": True, "metric": "revenue"},
        )

    tickers = sorted(annual_data)
    for i, left in enumerate(tickers):
        for right in tickers[i + 1 :]:
            if len([r for r in records if r["question_type"] == "cross_company_compare"]) >= 22:
                break
            left_doc = annuals[left]
            right_doc = annuals[right]
            left_data = annual_data[left]
            right_data = annual_data[right]
            left_value = metric_value(left_data, "revenue", 0)
            right_value = metric_value(right_data, "revenue", 0)
            if left_value is None or right_value is None:
                continue
            conclusion = "两家公司报告披露单位不同或无法直接比较，比较时应先统一单位。"
            if left_data["unit"] == right_data["unit"]:
                lv, rv = to_decimal(left_value), to_decimal(right_value)
                if lv is not None and rv is not None:
                    if lv > rv:
                        conclusion = f"在同为{left_data['unit']}口径下，{left_doc.company}更高。"
                    elif rv > lv:
                        conclusion = f"在同为{left_data['unit']}口径下，{right_doc.company}更高。"
                    else:
                        conclusion = f"在同为{left_data['unit']}口径下，两家公司相同。"
            add_record(
                records,
                question=f"{left_doc.company}和{right_doc.company}2025年年度报告披露的营业收入分别是多少？哪家公司更高？",
                answer=(
                    f"{left_doc.company}2025年营业收入为{left_value}，单位：{left_data['unit']}；"
                    f"{right_doc.company}2025年营业收入为{right_value}，单位：{right_data['unit']}。{conclusion}"
                ),
                evidences=[
                    metric_snippet_evidence(left_doc, left_data, "revenue"),
                    metric_snippet_evidence(right_doc, right_data, "revenue"),
                ],
                difficulty="multi_hop",
                question_type="cross_company_compare",
                metadata={"requires_all_evidence": True, "metric": "revenue"},
            )
        if len([r for r in records if r["question_type"] == "cross_company_compare"]) >= 22:
            break

    hard_negative_tickers = sorted(annual_data)[:12]
    for ticker in hard_negative_tickers:
        doc = annuals[ticker]
        data = annual_data[ticker]
        add_record(
            records,
            question=f"{doc.company}2025年年度报告是否披露了2027年净利润预测的具体金额？",
            answer=f"未发现{doc.company}2025年年度报告披露2027年净利润预测的具体金额；回答时应说明未披露，不应编造预测数值。",
            evidences=[data["evidence"]],
            difficulty="negative",
            question_type="unanswerable",
            eval_type="abstention",
            metadata={"hard_negative": True, "expected_behavior": "answer_not_disclosed"},
        )

    # Deterministic fill: add harder phrasing for table metrics until target_count.
    cursor = 0
    table_candidates = [
        (ticker, metric)
        for ticker in sorted(annual_data)
        for metric in ["revenue", "net_profit", "cashflow", "total_assets"]
        if metric_value(annual_data[ticker], metric, 0) is not None
    ]
    metric_labels = {
        "revenue": "营业收入",
        "net_profit": "归属于上市公司股东的净利润",
        "cashflow": "经营活动产生的现金流量净额",
        "total_assets": "总资产",
    }
    while len(records) < target_count and table_candidates:
        ticker, metric = table_candidates[cursor % len(table_candidates)]
        cursor += 1
        doc = annuals[ticker]
        data = annual_data[ticker]
        label = metric_labels[metric]
        value = metric_value(data, metric, 0)
        add_record(
            records,
            question=f"只根据{doc.company}2025年年度报告，回答其2025年的{label}，并保留报告披露单位。",
            answer=f"{doc.company}2025年{label}为{value}，单位：{data['unit']}。",
            evidences=[metric_snippet_evidence(doc, data, metric)],
            difficulty="medium",
            question_type="hard_negative_table_numeric",
            metadata={"metric": metric, "hard_negative_hint": "same company reports may contain quarterly or summary distractors"},
        )

    return records[:target_count]


def write_outputs(records: list[dict[str, Any]], output_dir: Path, root: Path) -> None:
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
            f.write(json.dumps({
                "query_id": record["id"],
                "question": record["question"],
                "difficulty": record["difficulty"],
                "question_type": record["question_type"],
            }, ensure_ascii=False) + "\n")

    seen_evidence: dict[str, dict[str, Any]] = {}
    with qrels_path.open("w", encoding="utf-8") as f:
        f.write("query_id\tevidence_id\trelevance\n")
        for record in records:
            for ctx in record["positive_contexts"]:
                f.write(f"{record['id']}\t{ctx['evidence_id']}\t{ctx.get('relevance', 1)}\n")
                seen_evidence.setdefault(ctx["evidence_id"], ctx)

    with evidence_path.open("w", encoding="utf-8") as f:
        for evidence in seen_evidence.values():
            f.write(json.dumps(evidence, ensure_ascii=False) + "\n")

    summary = {
        "version": "v2",
        "root": str(root),
        "count": len(records),
        "outputs": {
            "golden_questions": str(golden_path),
            "queries": str(queries_path),
            "qrels": str(qrels_path),
            "evidence_catalog": str(evidence_path),
        },
        "by_difficulty": dict(Counter(record["difficulty"] for record in records)),
        "by_question_type": dict(Counter(record["question_type"] for record in records)),
        "multi_evidence_count": sum(1 for record in records if len(record["positive_contexts"]) > 1),
        "abstention_count": sum(1 for record in records if record.get("eval_type") == "abstention"),
    }
    manifest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a stricter v2 Chinese finance retrieval eval set.")
    parser.add_argument("--root", type=Path, default=Path("data/chinese_finance_kb/pdf_cninfo_10companies"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/eval_cn_finance/v2"))
    parser.add_argument("--count", type=int, default=150)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = build_records(args.root, args.count)
    write_outputs(records, args.output_dir, args.root)


if __name__ == "__main__":
    main()
