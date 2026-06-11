from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Iterable

import requests


SEC_BASE = "https://www.sec.gov"
DATA_BASE = "https://data.sec.gov"

DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA"]
DEFAULT_FORMS = ["10-K", "10-Q", "8-K", "DEF 14A"]


def get_json(url: str, headers: dict[str, str], delay_seconds: float) -> dict:
    response = requests.get(url, headers=headers, timeout=45)
    response.raise_for_status()
    time.sleep(delay_seconds)
    return response.json()


def download_file(url: str, target: Path, headers: dict[str, str], delay_seconds: float) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, headers=headers, timeout=90)
    response.raise_for_status()
    target.write_bytes(response.content)
    time.sleep(delay_seconds)
    return len(response.content)


def get_ticker_to_cik(headers: dict[str, str], delay_seconds: float) -> dict[str, str]:
    data = get_json(f"{SEC_BASE}/files/company_tickers.json", headers, delay_seconds)
    return {
        str(item["ticker"]).upper(): str(item["cik_str"]).zfill(10)
        for item in data.values()
    }


def archive_url(cik10: str, accession: str, filename: str) -> str:
    cik_no_zero = str(int(cik10))
    accession_no_dash = accession.replace("-", "")
    return f"{SEC_BASE}/Archives/edgar/data/{cik_no_zero}/{accession_no_dash}/{filename}"


def filing_index_url(cik10: str, accession: str) -> str:
    cik_no_zero = str(int(cik10))
    accession_no_dash = accession.replace("-", "")
    return f"{SEC_BASE}/Archives/edgar/data/{cik_no_zero}/{accession_no_dash}/index.json"


def safe_form_name(form: str) -> str:
    return form.replace(" ", "_").replace("/", "_")


def iter_recent_filings(submissions: dict) -> Iterable[dict]:
    recent = submissions.get("filings", {}).get("recent", {})
    keys = [
        "form",
        "filingDate",
        "accessionNumber",
        "primaryDocument",
        "primaryDocDescription",
    ]
    values = [recent.get(key, []) for key in keys]
    for row in zip(*values):
        yield dict(zip(keys, row))


def save_companyfacts(
    *,
    ticker: str,
    cik10: str,
    out_root: Path,
    headers: dict[str, str],
    delay_seconds: float,
) -> dict:
    url = f"{DATA_BASE}/api/xbrl/companyfacts/CIK{cik10}.json"
    target = out_root / "xbrl" / ticker / "companyfacts.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    facts = get_json(url, headers, delay_seconds)
    target.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ticker": ticker,
        "cik": cik10,
        "kind": "companyfacts",
        "source_url": url,
        "path": str(target),
        "bytes": target.stat().st_size,
    }


def collect_company(
    *,
    ticker: str,
    cik10: str,
    forms: set[str],
    max_per_form: int,
    out_root: Path,
    headers: dict[str, str],
    delay_seconds: float,
) -> list[dict]:
    submissions_url = f"{DATA_BASE}/submissions/CIK{cik10}.json"
    submissions = get_json(submissions_url, headers, delay_seconds)

    results: list[dict] = []
    counts: dict[str, int] = {}
    for filing in iter_recent_filings(submissions):
        form = str(filing["form"])
        if form not in forms:
            continue
        counts[form] = counts.get(form, 0)
        if counts[form] >= max_per_form:
            continue
        counts[form] += 1

        filing_date = str(filing["filingDate"])
        accession = str(filing["accessionNumber"])
        primary_document = str(filing["primaryDocument"])
        year = filing_date[:4] or "unknown_year"
        source_url = archive_url(cik10, accession, primary_document)
        target = (
            out_root
            / "filings"
            / ticker
            / safe_form_name(form)
            / year
            / f"{accession}_{primary_document}"
        )

        size = download_file(source_url, target, headers, delay_seconds)
        metadata = {
            "ticker": ticker,
            "cik": cik10,
            "form": form,
            "filing_date": filing_date,
            "accession": accession,
            "primary_document": primary_document,
            "primary_doc_description": filing.get("primaryDocDescription") or "",
            "source_url": source_url,
            "index_url": filing_index_url(cik10, accession),
            "local_path": str(target),
            "bytes": size,
        }
        target.with_suffix(target.suffix + ".meta.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        results.append(metadata)
        print(f"downloaded {ticker} {form} {filing_date} {target}")

    return results


def write_manifest(out_root: Path, items: list[dict]) -> None:
    manifest = {
        "source": "SEC EDGAR",
        "document_count": len([item for item in items if item.get("form")]),
        "companyfacts_count": len([item for item in items if item.get("kind") == "companyfacts"]),
        "items": items,
    }
    path = out_root / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download SEC EDGAR filings for FinRAG knowledge base.")
    parser.add_argument("--out", default="data/finance_kb/raw_sec")
    parser.add_argument("--tickers", nargs="*", default=DEFAULT_TICKERS)
    parser.add_argument("--forms", nargs="*", default=DEFAULT_FORMS)
    parser.add_argument("--max-per-form", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument(
        "--user-agent",
        default="WeiQuizFinRAG/0.1 contact@example.com",
        help="SEC requires a descriptive User-Agent with contact info.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_root = Path(args.out)
    tickers = [ticker.upper() for ticker in args.tickers]
    forms = {form.upper() for form in args.forms}
    headers = {"User-Agent": args.user_agent}

    ticker_to_cik = get_ticker_to_cik(headers, args.delay)
    all_items: list[dict] = []
    for ticker in tickers:
        cik10 = ticker_to_cik.get(ticker)
        if not cik10:
            print(f"skip unknown ticker: {ticker}")
            continue
        all_items.extend(
            collect_company(
                ticker=ticker,
                cik10=cik10,
                forms=forms,
                max_per_form=args.max_per_form,
                out_root=out_root,
                headers=headers,
                delay_seconds=args.delay,
            )
        )
        try:
            facts_item = save_companyfacts(
                ticker=ticker,
                cik10=cik10,
                out_root=out_root,
                headers=headers,
                delay_seconds=args.delay,
            )
            all_items.append(facts_item)
            print(f"downloaded {ticker} companyfacts {facts_item['path']}")
        except requests.HTTPError as exc:
            print(f"skip companyfacts for {ticker}: {exc}")

    write_manifest(out_root, all_items)
    print(f"done: {len(all_items)} items -> {out_root / 'manifest.json'}")


if __name__ == "__main__":
    main()
