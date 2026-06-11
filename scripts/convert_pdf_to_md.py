from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz


def extract_pdf_to_markdown(pdf_path: Path) -> tuple[str, int]:
    doc = fitz.open(pdf_path)
    parts: list[str] = []
    for page_index, page in enumerate(doc, start=1):
        text = page.get_text("text").strip()
        if not text:
            continue
        parts.append(f"## Page {page_index}\n\n{text}")
    return "\n\n---\n\n".join(parts).strip() + "\n", doc.page_count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert PDF files to Markdown with page markers.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_root = Path(args.input)
    output_root = Path(args.out)
    pdf_files = sorted(input_root.rglob("*.pdf"))
    if args.limit > 0:
        pdf_files = pdf_files[: args.limit]

    items: list[dict] = []
    for pdf_path in pdf_files:
        rel = pdf_path.relative_to(input_root)
        md_path = (output_root / rel).with_suffix(".md")
        md_path.parent.mkdir(parents=True, exist_ok=True)

        markdown, page_count = extract_pdf_to_markdown(pdf_path)
        meta_path = pdf_path.with_suffix(".meta.json")
        metadata = {}
        if meta_path.exists():
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))

        header = {
            "source_format": "pdf",
            "source_path": str(pdf_path),
            "page_count": page_count,
            **metadata,
        }
        md_path.write_text(
            "---\n"
            + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in header.items())
            + "\n---\n\n"
            + markdown,
            encoding="utf-8",
        )
        items.append(
            {
                "source_path": str(pdf_path),
                "local_path": str(md_path),
                "page_count": page_count,
                "bytes": md_path.stat().st_size,
                "document_type": metadata.get("document_type", ""),
                "ticker": metadata.get("ticker", ""),
            }
        )
        print(f"converted {pdf_path} -> {md_path}")

    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "manifest.json").write_text(
        json.dumps(
            {
                "source": "PDF converted to Markdown",
                "document_count": len(items),
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"done: {len(items)} Markdown documents -> {output_root / 'manifest.json'}")


if __name__ == "__main__":
    main()
