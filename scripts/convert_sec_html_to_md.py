from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup


def normalize_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def table_to_markdown(table) -> str:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = []
        for cell in tr.find_all(["th", "td"]):
            value = normalize_text(cell.get_text(" ", strip=True))
            cells.append(value)
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""

    max_cols = max(len(row) for row in rows)
    normalized = [row + [""] * (max_cols - len(row)) for row in rows]
    header = normalized[0]
    body = normalized[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * max_cols) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def html_to_markdown(html_text: str) -> str:
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    parts: list[str] = []
    for element in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "table"]):
        if element.name == "table":
            table_md = table_to_markdown(element)
            if table_md:
                parts.append(table_md)
            continue
        text = normalize_text(element.get_text(" ", strip=True))
        if not text:
            continue
        if element.name in {"h1", "h2", "h3", "h4"}:
            level = int(element.name[1])
            parts.append(f"{'#' * min(level, 4)} {text}")
        elif element.name == "li":
            parts.append(f"- {text}")
        else:
            parts.append(text)

    if not parts:
        parts.append(normalize_text(soup.get_text("\n", strip=True)))
    return "\n\n".join(part for part in parts if part).strip() + "\n"


def relative_output_path(input_path: Path, input_root: Path, output_root: Path) -> Path:
    rel = input_path.relative_to(input_root)
    return (output_root / rel).with_suffix(".md")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert SEC HTML filings to Markdown documents.")
    parser.add_argument("--input", default="data/finance_kb/raw_sec/filings")
    parser.add_argument("--out", default="data/finance_kb/markdown_sec")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_root = Path(args.input)
    output_root = Path(args.out)
    html_files = sorted(list(input_root.rglob("*.htm")) + list(input_root.rglob("*.html")))
    if args.limit > 0:
        html_files = html_files[: args.limit]

    items: list[dict] = []
    for input_path in html_files:
        output_path = relative_output_path(input_path, input_root, output_root)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        html_text = input_path.read_text(encoding="utf-8", errors="ignore")
        markdown_text = html_to_markdown(html_text)

        meta_path = input_path.with_suffix(input_path.suffix + ".meta.json")
        metadata = {}
        if meta_path.exists():
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))

        header = {
            "source_format": "sec_html",
            "source_path": str(input_path),
            **metadata,
        }
        output_path.write_text(
            "---\n"
            + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in header.items())
            + "\n---\n\n"
            + markdown_text,
            encoding="utf-8",
        )
        items.append(
            {
                "source_path": str(input_path),
                "local_path": str(output_path),
                "bytes": output_path.stat().st_size,
                "form": metadata.get("form", ""),
                "ticker": metadata.get("ticker", ""),
            }
        )
        print(f"converted {input_path} -> {output_path}")

    manifest = {
        "source": "SEC HTML converted to Markdown",
        "document_count": len(items),
        "items": items,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"done: {len(items)} Markdown documents -> {output_root / 'manifest.json'}")


if __name__ == "__main__":
    main()
