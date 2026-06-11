from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


CNINFO_BASE = "http://www.cninfo.com.cn"
CNINFO_STATIC = "http://static.cninfo.com.cn"
QUERY_URL = f"{CNINFO_BASE}/new/hisAnnouncement/query"

DEFAULT_COMPANIES = [
    {"ticker": "002594", "name": "BYD", "cn_name": "比亚迪", "org_id": "gssz0002594", "column": "szse"},
    {"ticker": "000333", "name": "Midea", "cn_name": "美的集团", "org_id": "gssz0000333", "column": "szse"},
    {"ticker": "000651", "name": "Gree", "cn_name": "格力电器", "org_id": "gssz0000651", "column": "szse"},
    {"ticker": "300750", "name": "CATL", "cn_name": "宁德时代", "org_id": "gssz0003750", "column": "szse"},
    {"ticker": "000858", "name": "Wuliangye", "cn_name": "五粮液", "org_id": "gssz0000858", "column": "szse"},
    {"ticker": "002415", "name": "Hikvision", "cn_name": "海康威视", "org_id": "gssz0002415", "column": "szse"},
    {"ticker": "600519", "name": "KweichowMoutai", "cn_name": "贵州茅台", "org_id": "gssh0600519", "column": "sse"},
    {"ticker": "601318", "name": "PingAn", "cn_name": "中国平安", "org_id": "gssh0601318", "column": "sse"},
    {"ticker": "600036", "name": "CMB", "cn_name": "招商银行", "org_id": "gssh0600036", "column": "sse"},
    {"ticker": "601012", "name": "LONGi", "cn_name": "隆基绿能", "org_id": "gssh0601012", "column": "sse"},
]

DEFAULT_CATEGORIES = {
    "category_ndbg_szsh": "annual_report",
    "category_bndbg_szsh": "semiannual_report",
    "category_yjdbg_szsh": "quarterly_report",
    "category_shzqgg_szsh": "shareholder_meeting",
    "category_zf_szsh": "refinancing",
    "category_gddh_szsh": "governance",
}


def headers() -> dict[str, str]:
    return {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "http://www.cninfo.com.cn/new/commonUrl/pageOfSearch?url=disclosure/list/search",
        "User-Agent": "WeiQuizFinRAG/0.1 contact@example.com",
        "X-Requested-With": "XMLHttpRequest",
    }


def query_announcements(
    *,
    company: dict[str, str],
    category: str,
    start_date: str,
    end_date: str,
    page_size: int,
    delay_seconds: float,
    searchkey: str = "",
    stock_override: str | None = None,
) -> list[dict[str, Any]]:
    stock = stock_override
    if stock is None:
        stock = f"{company['ticker']},{company['org_id']}" if company.get("org_id") else company["ticker"]
    payload = {
        "pageNum": "1",
        "pageSize": str(page_size),
        "column": company.get("column", "szse"),
        "tabName": "fulltext",
        "plate": "",
        "stock": stock,
        "searchkey": searchkey,
        "secid": "",
        "category": category,
        "trade": "",
        "seDate": f"{start_date}~{end_date}",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    response = requests.post(QUERY_URL, headers=headers(), data=payload, timeout=45)
    response.raise_for_status()
    time.sleep(delay_seconds)
    data = response.json()
    return data.get("announcements") or []


def query_announcements_with_fallback(
    *,
    company: dict[str, str],
    category: str,
    start_date: str,
    end_date: str,
    page_size: int,
    delay_seconds: float,
) -> list[dict[str, Any]]:
    attempts = [
        {"searchkey": "", "stock_override": None},
        {"searchkey": "", "stock_override": company["ticker"]},
        {"searchkey": company.get("cn_name", company["ticker"]), "stock_override": ""},
    ]
    for attempt in attempts:
        announcements = query_announcements(
            company=company,
            category=category,
            start_date=start_date,
            end_date=end_date,
            page_size=page_size,
            delay_seconds=delay_seconds,
            searchkey=attempt["searchkey"],
            stock_override=attempt["stock_override"],
        )
        if announcements:
            return announcements
    return []


def clean_title(title: str) -> str:
    title = title.replace("<em>", "").replace("</em>", "")
    return "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in title).strip()[:120]


def announcement_pdf_url(announcement: dict[str, Any]) -> str:
    adjunct_url = str(announcement.get("adjunctUrl") or "")
    if adjunct_url.startswith("http"):
        return adjunct_url
    return f"{CNINFO_STATIC}/{adjunct_url.lstrip('/')}"


def announcement_date(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        text = str(value or "").strip()
        return text[:10] if text else "unknown_date"
    if number > 10_000_000_000:
        number = number // 1000
    return datetime.fromtimestamp(number).strftime("%Y-%m-%d")


def download_pdf(url: str, target: Path, delay_seconds: float) -> int | None:
    response = requests.get(url, headers=headers(), timeout=90)
    time.sleep(delay_seconds)
    if response.status_code != 200:
        return None
    content = response.content
    if not content.startswith(b"%PDF"):
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return len(content)


def collect_company(
    *,
    company: dict[str, str],
    categories: dict[str, str],
    out_root: Path,
    start_date: str,
    end_date: str,
    max_per_category: int,
    page_size: int,
    delay_seconds: float,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for category, label in categories.items():
        try:
            announcements = query_announcements_with_fallback(
                company=company,
                category=category,
                start_date=start_date,
                end_date=end_date,
                page_size=page_size,
                delay_seconds=delay_seconds,
            )
        except Exception as exc:
            print(f"query failed {company['ticker']} {category}: {exc}")
            continue

        downloaded = 0
        for announcement in announcements:
            if downloaded >= max_per_category:
                break
            title = clean_title(str(announcement.get("announcementTitle") or "announcement"))
            date = announcement_date(announcement.get("announcementTime"))
            url = announcement_pdf_url(announcement)
            target = (
                out_root
                / company["ticker"]
                / label
                / f"{date}_{title}.pdf"
            )
            size = download_pdf(url, target, delay_seconds)
            if size is None:
                print(f"skip non-pdf {company['ticker']} {label}: {url}")
                continue
            item = {
                "ticker": company["ticker"],
                "company": company["name"],
                "category": category,
                "document_type": label,
                "title": title,
                "announcement_date": date,
                "source_url": url,
                "local_path": str(target),
                "bytes": size,
            }
            target.with_suffix(".meta.json").write_text(
                json.dumps(item, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            items.append(item)
            downloaded += 1
            print(f"downloaded {company['ticker']} {label} {date} {target}")

    return items


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Chinese A-share PDFs from CNINFO.")
    parser.add_argument("--out", default="data/chinese_finance_kb/pdf_cninfo")
    parser.add_argument("--start-date", default="2023-01-01")
    parser.add_argument("--end-date", default="2026-06-07")
    parser.add_argument("--max-per-category", type=int, default=2)
    parser.add_argument("--page-size", type=int, default=30)
    parser.add_argument("--delay", type=float, default=0.5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_root = Path(args.out)
    all_items: list[dict[str, Any]] = []
    for company in DEFAULT_COMPANIES:
        all_items.extend(
            collect_company(
                company=company,
                categories=DEFAULT_CATEGORIES,
                out_root=out_root,
                start_date=args.start_date,
                end_date=args.end_date,
                max_per_category=args.max_per_category,
                page_size=args.page_size,
                delay_seconds=args.delay,
            )
        )

    manifest = {
        "source": "CNINFO",
        "document_count": len(all_items),
        "items": all_items,
    }
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"done: {len(all_items)} Chinese PDFs -> {out_root / 'manifest.json'}")


if __name__ == "__main__":
    main()
