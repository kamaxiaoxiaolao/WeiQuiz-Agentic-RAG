"""Run OCR for one PDF and export Markdown/JSON audit files.

This script is intentionally independent from the main ingestion pipeline.
Install optional OCR dependencies first when needed:

    pip install paddleocr pymupdf

Example:

    python -m app.ingest.ocr_single_pdf --file data/docs/example.pdf --doc-id example_pdf
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from typing import Any, List, Optional

from app.config import settings


@dataclass
class OCRPage:
    page_no: int
    text: str
    text_chars: int
    line_count: int


def _safe_filename(name: str) -> str:
    name = name.strip() or "ocr_result"
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1F]", "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:120]


def _load_pymupdf() -> Any:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "Missing optional dependency 'pymupdf'. Install it with: pip install pymupdf"
        ) from exc
    return fitz


def _load_paddleocr() -> Any:
    # PaddlePaddle 3.x can hit oneDNN/PIR runtime issues on some Windows CPU
    # environments. OCR is an offline preprocessing path, so stability is more
    # important than CPU acceleration here.
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_enable_onednn", "0")
    os.environ.setdefault("FLAGS_enable_pir_api", "0")
    os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")

    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        raise RuntimeError(
            "Missing optional dependency 'paddleocr'. Install it with: pip install paddleocr"
        ) from exc
    return PaddleOCR


def _build_paddle_ocr_engine(
    PaddleOCR: Any,
    *,
    lang: str,
    det_model: str,
    rec_model: str,
) -> Any:
    try:
        return PaddleOCR(
            lang=lang,
            text_detection_model_name=det_model,
            text_recognition_model_name=rec_model,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
    except TypeError:
        # PaddleOCR 2.x does not support explicit model names here.
        return PaddleOCR(use_angle_cls=True, lang=lang)


def _ocr_result_to_lines(result: Any) -> List[str]:
    lines: List[str] = []
    if not result:
        return lines

    if isinstance(result, dict):
        rec_texts = result.get("rec_texts") or result.get("text") or []
        if isinstance(rec_texts, str):
            rec_texts = [rec_texts]
        for text in rec_texts:
            text = str(text).strip()
            if text:
                lines.append(text)
        if lines:
            return lines

    if isinstance(result, list) and result and isinstance(result[0], dict):
        for page_result in result:
            rec_texts = page_result.get("rec_texts") or []
            for text in rec_texts:
                text = str(text).strip()
                if text:
                    lines.append(text)
        return lines

    # PaddleOCR 2.x commonly returns: [[box, (text, score)], ...].
    # Some versions wrap page results in another list.
    candidates = result[0] if len(result) == 1 and isinstance(result[0], list) else result

    for item in candidates:
        if not item or not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        payload = item[1]
        if isinstance(payload, (list, tuple)) and payload:
            text = str(payload[0]).strip()
        else:
            text = str(payload).strip()
        if text:
            lines.append(text)

    return lines


def ocr_pdf(
    file_path: str,
    *,
    lang: str = "ch",
    dpi: int = 180,
    max_pages: Optional[int] = None,
    det_model: str = "PP-OCRv5_mobile_det",
    rec_model: str = "PP-OCRv5_mobile_rec",
) -> List[OCRPage]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)
    if not file_path.lower().endswith(".pdf"):
        raise ValueError(f"Not a PDF file: {file_path}")

    fitz = _load_pymupdf()
    PaddleOCR = _load_paddleocr()
    print(
        "[OCR] Creating PaddleOCR engine, "
        f"lang={lang}, det_model={det_model}, rec_model={rec_model}, dpi={dpi}, max_pages={max_pages}",
        flush=True,
    )
    engine = _build_paddle_ocr_engine(
        PaddleOCR,
        lang=lang,
        det_model=det_model,
        rec_model=rec_model,
    )
    print("[OCR] PaddleOCR engine ready", flush=True)

    pages: List[OCRPage] = []
    doc = fitz.open(file_path)
    page_count = len(doc)
    limit = min(page_count, max_pages) if max_pages else page_count
    matrix = fitz.Matrix(dpi / 72, dpi / 72)
    print(f"[OCR] Opened PDF: pages={page_count}, processing={limit}", flush=True)

    with tempfile.TemporaryDirectory(prefix="weiquiz_ocr_") as tmp_dir:
        for page_index in range(limit):
            print(f"[OCR] Rendering page {page_index + 1}/{limit}", flush=True)
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image_path = os.path.join(tmp_dir, f"page_{page_index + 1}.png")
            pix.save(image_path)
            print(f"[OCR] Running OCR page {page_index + 1}: {image_path}", flush=True)

            if hasattr(engine, "predict"):
                result = engine.predict(image_path)
            else:
                result = engine.ocr(image_path, cls=True)

            lines = _ocr_result_to_lines(result)
            print(f"[OCR] Page {page_index + 1} lines={len(lines)} chars={sum(len(line) for line in lines)}", flush=True)
            text = "\n".join(lines).strip()
            pages.append(
                OCRPage(
                    page_no=page_index + 1,
                    text=text,
                    text_chars=len(text),
                    line_count=len(lines),
                )
            )

    return pages


def pages_to_markdown(pages: List[OCRPage], *, doc_id: str, source_path: str) -> str:
    parts = [
        "---",
        f"doc_id: {doc_id}",
        f"source_path: {source_path}",
        "ocr_engine: paddleocr",
        "---",
        "",
    ]
    for page in pages:
        parts.append(f"## Page {page.page_no}")
        parts.append("")
        parts.append(page.text or "<empty>")
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def save_ocr_outputs(
    pages: List[OCRPage],
    *,
    out_dir: str,
    doc_id: str,
    source_path: str,
) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    base = _safe_filename(doc_id or os.path.splitext(os.path.basename(source_path))[0])
    md_path = os.path.join(out_dir, f"{base}.ocr.md")
    json_path = os.path.join(out_dir, f"{base}.ocr.json")
    print(f"[OCR] Saving outputs to {out_dir}", flush=True)

    summary = {
        "doc_id": doc_id,
        "source_path": source_path,
        "ocr_engine": "paddleocr",
        "page_count": len(pages),
        "total_text_chars": sum(page.text_chars for page in pages),
        "empty_page_count": sum(1 for page in pages if not page.text.strip()),
        "pages": [asdict(page) for page in pages],
    }

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(pages_to_markdown(pages, doc_id=doc_id, source_path=source_path))

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"[OCR] Saved markdown={md_path}", flush=True)
    print(f"[OCR] Saved json={json_path}", flush=True)

    return {"markdown": md_path, "json": json_path, "summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR one scanned PDF and export Markdown/JSON audit files.")
    parser.add_argument("--file", required=True, help="PDF file path.")
    parser.add_argument("--doc-id", default="", help="Document id used for output naming and front matter.")
    parser.add_argument("--out-dir", default=settings.ocr_output_dir, help="Output directory.")
    parser.add_argument("--det-model", default=settings.ocr_paddle_det_model, help="PaddleOCR detection model name.")
    parser.add_argument("--rec-model", default=settings.ocr_paddle_rec_model, help="PaddleOCR recognition model name.")
    args = parser.parse_args()

    source_path = args.file.replace("\\", "/")
    doc_id = args.doc_id or _safe_filename(os.path.splitext(os.path.basename(args.file))[0])
    max_pages = settings.ocr_max_pages if settings.ocr_max_pages > 0 else None
    pages = ocr_pdf(
        args.file,
        lang=settings.ocr_paddle_lang,
        dpi=settings.ocr_pdf_dpi,
        max_pages=max_pages,
        det_model=args.det_model,
        rec_model=args.rec_model,
    )
    outputs = save_ocr_outputs(pages, out_dir=args.out_dir, doc_id=doc_id, source_path=source_path)

    summary = outputs["summary"]
    print("--- OCR Completed ---")
    print(f"doc_id: {summary['doc_id']}")
    print(f"pages: {summary['page_count']}")
    print(f"total_text_chars: {summary['total_text_chars']}")
    print(f"empty_page_count: {summary['empty_page_count']}")
    print(f"markdown: {outputs['markdown']}")
    print(f"json: {outputs['json']}")


if __name__ == "__main__":
    main()
