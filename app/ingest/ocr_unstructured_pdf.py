"""Run Unstructured OCR for one PDF and export Markdown/JSON audit files.

This script validates the OCR path that best matches the main parser because
it uses unstructured.partition.pdf.partition_pdf and maps elements to blocks.

Example:

    python -m app.ingest.ocr_unstructured_pdf --file data/docs/example.pdf --doc-id example_pdf --max-pages 3
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from app.config import settings
from app.ingest.document_parser import (
    Block,
    _map_unstructured_element_to_block,
    blocks_to_markdown,
    clean_blocks_by_file_type,
    fix_titles,
)


def _safe_filename(name: str) -> str:
    from app.ingest.document_parser import _safe_filename as parser_safe_filename

    return parser_safe_filename(name)


def _partition_pdf_with_ocr(
    file_path: str,
    *,
    strategy: str,
    infer_table_structure: bool,
    languages: List[str],
    ocr_languages: Optional[str],
) -> List[Any]:
    try:
        from unstructured.partition.pdf import partition_pdf
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'unstructured[pdf]'. Install project dependencies first."
        ) from exc

    return partition_pdf(
        filename=file_path,
        strategy=strategy,
        infer_table_structure=infer_table_structure,
        languages=languages,
        ocr_languages=ocr_languages,
    )


def unstructured_ocr_pdf_to_blocks(
    file_path: str,
    *,
    doc_id: str,
    source_path: str,
    strategy: str = "ocr_only",
    infer_table_structure: bool = False,
    languages: Optional[List[str]] = None,
    ocr_languages: Optional[str] = None,
    max_pages: Optional[int] = None,
) -> List[Block]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)
    if not file_path.lower().endswith(".pdf"):
        raise ValueError(f"Not a PDF file: {file_path}")

    lang_list = languages if languages is not None else ["chi_sim", "eng"]
    elements = _partition_pdf_with_ocr(
        file_path,
        strategy=strategy,
        infer_table_structure=infer_table_structure,
        languages=lang_list,
        ocr_languages=ocr_languages,
    )

    blocks: List[Block] = []
    for el in elements:
        block = _map_unstructured_element_to_block(el, doc_id=doc_id, source_path=source_path)
        if block is None:
            continue
        if max_pages is not None and block.page_no is not None and block.page_no > max_pages:
            continue
        blocks.append(block)

    return blocks


def _block_to_json(block: Block) -> Dict[str, Any]:
    data = asdict(block)
    extra_info = data.get("extra_info") or {}
    if "coordinates" in extra_info and extra_info["coordinates"] is not None:
        extra_info["coordinates"] = str(extra_info["coordinates"])
    data["extra_info"] = extra_info
    return data


def save_ocr_outputs(
    blocks: List[Block],
    *,
    out_dir: str,
    doc_id: str,
    source_path: str,
    strategy: str,
) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    base = _safe_filename(doc_id or os.path.splitext(os.path.basename(source_path))[0])
    md_path = os.path.join(out_dir, f"{base}.unstructured_ocr.md")
    json_path = os.path.join(out_dir, f"{base}.unstructured_ocr.json")

    markdown = blocks_to_markdown(blocks)
    text_chars = sum(len((block.text or "").strip()) for block in blocks)
    summary = {
        "doc_id": doc_id,
        "source_path": source_path,
        "ocr_engine": "unstructured",
        "ocr_strategy": strategy,
        "block_count": len(blocks),
        "total_text_chars": text_chars,
        "pages": sorted({block.page_no for block in blocks if block.page_no is not None}),
        "blocks": [_block_to_json(block) for block in blocks],
    }

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return {"markdown": md_path, "json": json_path, "summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR one PDF with Unstructured and export Markdown/JSON audit files.")
    parser.add_argument("--file", required=True, help="PDF file path.")
    parser.add_argument("--doc-id", default="", help="Document id used for output naming and metadata.")
    parser.add_argument("--out-dir", default=settings.ocr_output_dir, help="Output directory.")
    args = parser.parse_args()

    source_path = args.file.replace("\\", "/")
    doc_id = args.doc_id or _safe_filename(os.path.splitext(os.path.basename(args.file))[0])
    languages = [item.strip() for item in settings.ocr_languages.split(",") if item.strip()]
    max_pages = settings.ocr_max_pages if settings.ocr_max_pages > 0 else None

    blocks = unstructured_ocr_pdf_to_blocks(
        args.file,
        doc_id=doc_id,
        source_path=source_path,
        strategy=settings.ocr_strategy,
        infer_table_structure=settings.ocr_infer_table_structure,
        languages=languages,
        ocr_languages=settings.ocr_tesseract_languages,
        max_pages=max_pages,
    )
    blocks = clean_blocks_by_file_type(blocks, ".pdf")
    blocks = fix_titles(blocks)
    outputs = save_ocr_outputs(
        blocks,
        out_dir=args.out_dir,
        doc_id=doc_id,
        source_path=source_path,
        strategy=settings.ocr_strategy,
    )

    summary = outputs["summary"]
    print("--- Unstructured OCR Completed ---")
    print(f"doc_id: {summary['doc_id']}")
    print(f"strategy: {summary['ocr_strategy']}")
    print(f"blocks: {summary['block_count']}")
    print(f"total_text_chars: {summary['total_text_chars']}")
    print(f"markdown: {outputs['markdown']}")
    print(f"json: {outputs['json']}")


if __name__ == "__main__":
    main()
