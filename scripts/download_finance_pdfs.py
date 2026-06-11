from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests


DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "AMD", "TSLA"]
DEFAULT_YEARS = [2024, 2023, 2022]


def annualreports_url(ticker: str, year: int) -> str:
    ticker = ticker.upper()
    bucket = ticker[0].lower()
    return f"https://www.annualreports.com/HostedData/AnnualReportArchive/{bucket}/NASDAQ_{ticker}_{year}.pdf"


def download_pdf(url: str, target: Path, headers: dict[str, str], delay_seconds: float) -> int | None:
    response = requests.get(url, headers=headers, timeout=90)
    time.sleep(delay_seconds)
    if response.status_code != 200:
        return None
    content = response.content
    if not content.startswith(b"%PDF"):
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return len(content)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download annual report PDFs for finance knowledge base.")
    parser.add_argument("--out", default="data/finance_kb/pdf")
    parser.add_argument("--tickers", nargs="*", default=DEFAULT_TICKERS)
    parser.add_argument("--years", nargs="*", type=int, default=DEFAULT_YEARS)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument(
        "--user-agent",
        default="WeiQuizFinRAG/0.1 contact@example.com",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out_root = Path(args.out)
    headers = {"User-Agent": args.user_agent}
    manifest_items: list[dict] = []

    for ticker in [item.upper() for item in args.tickers]:
        for year in args.years:
            url = annualreports_url(ticker, year)
            target = out_root / ticker / f"{ticker}_{year}_annual_report.pdf"
            size = download_pdf(url, target, headers, args.delay)
            if size is None:
                print(f"skip {ticker} {year}: {url}")
                continue
            item = {
                "ticker": ticker,
                "year": year,
                "document_type": "annual_report_pdf",
                "source_url": url,
                "local_path": str(target),
                "bytes": size,
            }
            manifest_items.append(item)
            target.with_suffix(".meta.json").write_text(
                json.dumps(item, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"downloaded {ticker} {year} {target}")

    manifest = {
        "source": "AnnualReports.com",
        "document_count": len(manifest_items),
        "items": manifest_items,
    }
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"done: {len(manifest_items)} PDFs -> {out_root / 'manifest.json'}")


if __name__ == "__main__":
    main()
