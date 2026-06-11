from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

import fitz


COMPANY_NAMES = {
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

NUM_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})*(?:\.\d+)?%?$|^-?\d+(?:\.\d+)?%?$")


@dataclass
class DocInfo:
    ticker: str
    company: str
    title: str
    document_type: str
    announcement_date: str
    path: Path
    rel_path: str


def load_meta_docs(root: Path) -> list[DocInfo]:
    docs: list[DocInfo] = []
    for meta_path in sorted(root.rglob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        path = Path(meta["local_path"])
        if not path.exists():
            continue
        ticker = meta["ticker"]
        docs.append(
            DocInfo(
                ticker=ticker,
                company=COMPANY_NAMES.get(ticker, meta.get("company", ticker)),
                title=meta.get("title", path.stem),
                document_type=meta.get("document_type", ""),
                announcement_date=meta.get("announcement_date", ""),
                path=path,
                rel_path=path.relative_to(root).as_posix(),
            )
        )
    return docs


def is_full_chinese_report(doc: DocInfo) -> bool:
    title = doc.title
    return "摘要" not in title and "英文" not in title and "英文版" not in title


def choose_annuals(docs: Iterable[DocInfo]) -> dict[str, DocInfo]:
    selected: dict[str, DocInfo] = {}
    for doc in docs:
        if doc.document_type == "annual_report" and is_full_chinese_report(doc):
            selected.setdefault(doc.ticker, doc)
    return selected


def choose_q1_reports(docs: Iterable[DocInfo]) -> dict[str, DocInfo]:
    selected: dict[str, DocInfo] = {}
    for doc in docs:
        if doc.document_type != "quarterly_report" or not is_full_chinese_report(doc):
            continue
        if "2026" not in doc.title:
            continue
        if doc.ticker == "601318" and "平安银行" in doc.title:
            continue
        selected.setdefault(doc.ticker, doc)
    return selected


def page_text(doc: fitz.Document, page_no: int) -> str:
    return doc.load_page(page_no - 1).get_text()


def compact_evidence(text: str, limit: int = 750) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text[:limit].strip()


def split_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def find_page(pdf: fitz.Document, required: Iterable[str], max_pages: int | None = None) -> tuple[int, str] | None:
    required = list(required)
    page_count = pdf.page_count if max_pages is None else min(pdf.page_count, max_pages)
    for idx in range(page_count):
        text = page_text(pdf, idx + 1)
        if all(keyword in text for keyword in required):
            return idx + 1, text
    return None


def extract_after_label(lines: list[str], label: str, skip_values: set[str] | None = None) -> str | None:
    skip_values = skip_values or set()
    for idx, line in enumerate(lines):
        if line == label or label in line:
            tail = line.replace(label, "").strip()
            if tail and tail not in skip_values:
                return tail
            for value in lines[idx + 1 : idx + 8]:
                if value not in skip_values:
                    return value
    return None


def numeric_values_after(lines: list[str], label_options: Iterable[str], scan: int = 18) -> list[str]:
    label_options = list(label_options)
    for idx, line in enumerate(lines):
        if not any(line == label or line.endswith(label) or label in line for label in label_options):
            continue
        values: list[str] = []
        for value in lines[idx + 1 : idx + 1 + scan]:
            if NUM_RE.match(value) or value == "不适用" or "减少" in value or "增加" in value:
                values.append(value)
        if values:
            return values
    return []


def detect_unit(text: str) -> str:
    if "（千元）" in text or "(千元)" in text or "单位：千元" in text:
        return "千元"
    if "人民币百万元" in text or "单位：百万元" in text or "（百万元）" in text or "(百万元)" in text:
        return "百万元"
    if "单位：元" in text or "（元）" in text or "(元)" in text:
        return "元"
    return "以报告披露单位为准"


def to_decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def annual_metrics(doc_info: DocInfo) -> dict[str, object] | None:
    with fitz.open(str(doc_info.path)) as pdf:
        best: tuple[int, str] | None = None
        for idx in range(min(pdf.page_count, 45)):
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
    revenue = numeric_values_after(lines, ["营业收入", "营业总收入"])
    profit = numeric_values_after(
        lines,
        [
            "归属于上市公司股东的净利润",
            "归属于母公司股东的净利润",
            "归属于上市公司股东净利润",
        ],
    )
    if len(revenue) < 2:
        return None
    unit = detect_unit(text)
    return {
        "page": page_no,
        "evidence": compact_evidence(text, 900),
        "unit": unit,
        "revenue_2025": revenue[0],
        "revenue_2024": revenue[1] if len(revenue) > 1 else None,
        "revenue_change": revenue[2] if len(revenue) > 2 else None,
        "profit_2025": profit[0] if profit else None,
        "profit_2024": profit[1] if len(profit) > 1 else None,
    }


def q1_metrics(doc_info: DocInfo) -> dict[str, object] | None:
    with fitz.open(str(doc_info.path)) as pdf:
        best: tuple[int, str] | None = None
        for idx in range(min(pdf.page_count, 20)):
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
    revenue = numeric_values_after(lines, ["营业收入", "营业总收入"])
    if not revenue:
        return None
    unit = detect_unit(text)
    return {
        "page": page_no,
        "evidence": compact_evidence(text, 800),
        "unit": unit,
        "q1_revenue": revenue[0],
    }


def company_info(doc_info: DocInfo) -> dict[str, object] | None:
    with fitz.open(str(doc_info.path)) as pdf:
        result = find_page(pdf, ["公司的法定代表人"], max_pages=20)
    if not result:
        return None
    page_no, text = result
    lines = split_lines(text)
    legal_rep = extract_after_label(lines, "公司的法定代表人")
    if not legal_rep:
        return None
    website = extract_after_label(lines, "公司网址")
    registered_address = extract_after_label(lines, "公司注册地址")
    return {
        "page": page_no,
        "evidence": compact_evidence(text, 750),
        "legal_rep": legal_rep,
        "website": website,
        "registered_address": registered_address,
    }


def make_record(
    idx: int,
    question: str,
    answer: str,
    doc: DocInfo | None,
    page: int | None,
    evidence: str,
    difficulty: str,
    question_type: str,
    extra_files: list[str] | None = None,
    extra_pages: list[int] | None = None,
    extra_evidence: str | None = None,
) -> dict:
    files = [] if doc is None else [doc.rel_path]
    pages = [] if page is None else [page]
    if extra_files:
        files.extend(extra_files)
    if extra_pages:
        pages.extend(extra_pages)
    if extra_evidence:
        evidence = evidence + "\n\n" + extra_evidence
    return {
        "id": f"cn_fin_{idx:03d}",
        "question": question,
        "answer": answer,
        "company": None if doc is None else doc.company,
        "stock_code": None if doc is None else doc.ticker,
        "doc_type": None if doc is None else doc.document_type,
        "difficulty": difficulty,
        "question_type": question_type,
        "gold_files": files,
        "gold_pages": pages,
        "gold_evidence": evidence,
        "eval_type": "exact_or_semantic",
    }


def build_dataset(root: Path, limit: int = 50) -> list[dict]:
    docs = load_meta_docs(root)
    annuals = choose_annuals(docs)
    q1s = choose_q1_reports(docs)
    annual_data = {ticker: annual_metrics(doc) for ticker, doc in annuals.items()}
    annual_data = {ticker: data for ticker, data in annual_data.items() if data}
    q1_data = {ticker: q1_metrics(doc) for ticker, doc in q1s.items()}
    q1_data = {ticker: data for ticker, data in q1_data.items() if data}
    info_data = {ticker: company_info(doc) for ticker, doc in annuals.items()}
    info_data = {ticker: data for ticker, data in info_data.items() if data}

    records: list[dict] = []

    def add(*args, **kwargs) -> None:
        if len(records) < limit:
            records.append(make_record(len(records) + 1, *args, **kwargs))

    for ticker in sorted(info_data):
        doc = annuals[ticker]
        data = info_data[ticker]
        add(
            f"{doc.company}2025年年度报告披露的公司法定代表人是谁？",
            f"{doc.company}2025年年度报告披露的公司法定代表人为{data['legal_rep']}。",
            doc,
            int(data["page"]),
            str(data["evidence"]),
            "simple",
            "company_profile",
        )

    for ticker in sorted(annual_data):
        doc = annuals[ticker]
        data = annual_data[ticker]
        add(
            f"{doc.company}2025年年度报告中披露的2025年营业收入是多少？",
            f"{doc.company}2025年营业收入为{data['revenue_2025']}，单位：{data['unit']}。",
            doc,
            int(data["page"]),
            str(data["evidence"]),
            "simple",
            "table_numeric",
        )

    for ticker in sorted(annual_data)[:8]:
        doc = annuals[ticker]
        data = annual_data[ticker]
        if not data.get("profit_2025"):
            continue
        add(
            f"{doc.company}2025年年度报告中归属于上市公司股东的净利润是多少？",
            f"{doc.company}2025年归属于上市公司股东的净利润为{data['profit_2025']}，单位：{data['unit']}。",
            doc,
            int(data["page"]),
            str(data["evidence"]),
            "simple",
            "table_numeric",
        )

    for ticker in sorted(annual_data)[:7]:
        doc = annuals[ticker]
        data = annual_data[ticker]
        add(
            f"{doc.company}2025年营业收入相比2024年是增长还是下降？变化比例是多少？",
            (
                f"{doc.company}2025年营业收入为{data['revenue_2025']}，2024年为{data['revenue_2024']}，"
                f"报告披露的同比变化为{data['revenue_change']}，单位：{data['unit']}。"
            ),
            doc,
            int(data["page"]),
            str(data["evidence"]),
            "complex",
            "same_doc_multi_hop",
        )

    for ticker in sorted(set(annual_data) & set(q1_data))[:5]:
        annual_doc = annuals[ticker]
        q1_doc = q1s[ticker]
        annual = annual_data[ticker]
        q1 = q1_data[ticker]
        add(
            f"{annual_doc.company}2025年全年营业收入和2026年第一季度营业收入分别是多少？",
            (
                f"{annual_doc.company}2025年全年营业收入为{annual['revenue_2025']}，单位：{annual['unit']}；"
                f"2026年第一季度营业收入为{q1['q1_revenue']}，单位：{q1['unit']}。"
            ),
            annual_doc,
            int(annual["page"]),
            str(annual["evidence"]),
            "multi_hop",
            "cross_doc_multi_hop",
            extra_files=[q1_doc.rel_path],
            extra_pages=[int(q1["page"])],
            extra_evidence=str(q1["evidence"]),
        )

    pairs = [
        ("600519", "000858"),
        ("000333", "000651"),
        ("002594", "300750"),
        ("600036", "601318"),
        ("002415", "601012"),
    ]
    for left, right in pairs:
        if left not in annual_data or right not in annual_data:
            continue
        left_doc = annuals[left]
        right_doc = annuals[right]
        left_data = annual_data[left]
        right_data = annual_data[right]
        conclusion = "两者单位可能因报告口径不同，比较时应先统一单位。"
        if left_data["unit"] == right_data["unit"]:
            left_value = to_decimal(left_data["revenue_2025"])
            right_value = to_decimal(right_data["revenue_2025"])
            if left_value is not None and right_value is not None:
                if left_value > right_value:
                    conclusion = f"在同为{left_data['unit']}的口径下，{left_doc.company}更高。"
                elif right_value > left_value:
                    conclusion = f"在同为{left_data['unit']}的口径下，{right_doc.company}更高。"
                else:
                    conclusion = f"在同为{left_data['unit']}的口径下，两家公司相同。"
        add(
            f"{left_doc.company}和{right_doc.company}2025年年度报告披露的营业收入分别是多少？哪家公司更高？",
            (
                f"{left_doc.company}2025年营业收入为{left_data['revenue_2025']}，单位：{left_data['unit']}；"
                f"{right_doc.company}2025年营业收入为{right_data['revenue_2025']}，单位：{right_data['unit']}。"
                f"{conclusion}"
            ),
            left_doc,
            int(left_data["page"]),
            str(left_data["evidence"]),
            "multi_hop",
            "cross_company_compare",
            extra_files=[right_doc.rel_path],
            extra_pages=[int(right_data["page"])],
            extra_evidence=str(right_data["evidence"]),
        )

    for ticker in sorted(annual_data)[:5]:
        doc = annuals[ticker]
        with fitz.open(str(doc.path)) as pdf:
            result = find_page(pdf, ["前瞻性陈述"], max_pages=8)
        if not result:
            page_no, evidence = int(annual_data[ticker]["page"]), str(annual_data[ticker]["evidence"])
        else:
            page_no, evidence = result[0], compact_evidence(result[1], 750)
        add(
            f"{doc.company}2025年年度报告是否披露了2027年净利润预测的具体金额？",
            f"未发现{doc.company}2025年年度报告披露2027年净利润预测的具体金额；回答时应说明未披露，不能编造预测数值。",
            doc,
            page_no,
            evidence,
            "negative",
            "unanswerable",
        )

    for ticker in sorted(set(q1_data) - set(sorted(set(annual_data) & set(q1_data))[:5])):
        doc = q1s[ticker]
        data = q1_data[ticker]
        add(
            f"{doc.company}2026年第一季度报告披露的营业收入是多少？",
            f"{doc.company}2026年第一季度营业收入为{data['q1_revenue']}，单位：{data['unit']}。",
            doc,
            int(data["page"]),
            str(data["evidence"]),
            "simple",
            "quarterly_table_numeric",
        )

    for ticker in sorted(info_data):
        doc = annuals[ticker]
        data = info_data[ticker]
        if not data.get("website"):
            continue
        add(
            f"{doc.company}2025年年度报告披露的公司网址是什么？",
            f"{doc.company}2025年年度报告披露的公司网址为{data['website']}。",
            doc,
            int(data["page"]),
            str(data["evidence"]),
            "simple",
            "company_profile",
        )

    for ticker in sorted(info_data):
        doc = annuals[ticker]
        data = info_data[ticker]
        if not data.get("registered_address"):
            continue
        add(
            f"{doc.company}2025年年度报告披露的公司注册地址是什么？",
            f"{doc.company}2025年年度报告披露的公司注册地址为{data['registered_address']}。",
            doc,
            int(data["page"]),
            str(data["evidence"]),
            "simple",
            "company_profile",
        )

    return records[:limit]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a 50-item Chinese finance golden eval set.")
    parser.add_argument("--root", default="data/chinese_finance_kb/pdf_cninfo_10companies")
    parser.add_argument("--output", default="data/eval_cn_finance/golden_questions.jsonl")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    root = Path(args.root)
    records = build_dataset(root=root, limit=args.limit)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = {
        "output": str(output),
        "count": len(records),
        "by_difficulty": {},
        "by_question_type": {},
    }
    for record in records:
        summary["by_difficulty"][record["difficulty"]] = summary["by_difficulty"].get(record["difficulty"], 0) + 1
        summary["by_question_type"][record["question_type"]] = (
            summary["by_question_type"].get(record["question_type"], 0) + 1
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
