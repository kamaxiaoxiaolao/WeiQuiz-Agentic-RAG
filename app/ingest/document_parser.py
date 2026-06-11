from __future__ import annotations

import argparse
import html
from html.parser import HTMLParser
import logging
import os
import re
from dataclasses import dataclass, replace
from typing import Optional, Literal, List, Dict, Any

from llama_index.core.schema import Document

logger = logging.getLogger(__name__)

BlockType = Literal["title", "text", "list_item", "table", "image", "unknown"]


@dataclass(frozen=True)
class Block:
    block_type: BlockType
    text: str
    page_no: Optional[int]
    source_path: str
    doc_id: str
    html: Optional[str] = None
    extra_info: Optional[Dict[str, Any]] = None


def _normalize_suffix(file_path: str) -> str:
    return os.path.splitext(file_path)[1].lower()


def _read_text_file(file_path: str) -> str:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()


def _fallback_pypdf_to_blocks(file_path: str, *, doc_id: str, source_path: str) -> List[Block]:
    from pypdf import PdfReader

    reader = PdfReader(file_path)
    blocks: List[Block] = []
    for i, page in enumerate(reader.pages, start=1):
        page_text = (page.extract_text() or "").strip()
        if not page_text:
            continue
        blocks.append(
            Block(
                block_type="text",
                text=page_text,
                page_no=i,
                source_path=source_path,
                doc_id=doc_id,
            )
        )
    return blocks


def probe_pdf_text_layer(
    file_path: str,
    *,
    probe_pages: int = 3,
    scanned_threshold_chars: int = 100,
) -> Dict[str, Any]:
    """Probe whether a PDF has enough extractable text to skip OCR."""
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)
    if _normalize_suffix(file_path) != ".pdf":
        raise ValueError(f"Not a PDF file: {file_path}")

    from pypdf import PdfReader

    reader = PdfReader(file_path)
    total_pages = len(reader.pages)
    pages_to_probe = min(probe_pages, total_pages)
    text_chars = 0

    for page in reader.pages[:pages_to_probe]:
        text = page.extract_text() or ""
        text_chars += len(text.strip())

    is_scanned = pages_to_probe > 0 and text_chars < scanned_threshold_chars
    return {
        "pdf_probe_pages": pages_to_probe,
        "pdf_total_pages": total_pages,
        "pdf_text_probe_chars": text_chars,
        "pdf_scanned_threshold_chars": scanned_threshold_chars,
        "is_scanned_pdf": is_scanned,
        "ocr_required": is_scanned,
        "ocr_status": "not_configured" if is_scanned else "not_required",
    }


def _map_unstructured_element_to_block(
    el: Any, *, doc_id: str, source_path: str
) -> Optional[Block]:
    category = (getattr(el, "category", None) or "unknown").lower()
    text = (getattr(el, "text", None) or "").strip()
    meta = getattr(el, "metadata", None)

    page_no = getattr(meta, "page_number", None) if meta else None
    html_content = getattr(meta, "text_as_html", None) if meta else None

    if category == "title":
        b_type: BlockType = "title"
    elif category == "listitem" or "list" in category:
        b_type = "list_item"
    elif category == "table":
        b_type = "table"
    elif category in ("image", "figure", "figurecaption", "graphic"):
        b_type = "image"
    elif category in ("text", "narrativetext", "uncategorizedtext"):
        b_type = "text"
    else:
        b_type = "unknown"

    if not text and not html_content and b_type != "image":
        return None

    return Block(
        block_type=b_type,
        text=text,
        page_no=page_no,
        source_path=source_path,
        doc_id=doc_id,
        html=html_content,
        extra_info={
            "category": category,
            "coordinates": getattr(meta, "coordinates", None) if meta else None,
        },
    )


def parse_pdf_to_blocks(
    file_path: str,
    *,
    doc_id: str,
    source_path: str,
    use_hi_res: bool = False,
    infer_table_structure: bool = False,
    languages: Optional[List[str]] = None,
    ocr_languages: Optional[str] = None,
) -> List[Block]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)
    if _normalize_suffix(file_path) != ".pdf":
        raise ValueError(f"Not a PDF file: {file_path}")

    try:
        from unstructured.partition.pdf import partition_pdf
    except ImportError as e:
        logger.warning("unstructured not available, fallback to pypdf: %s", e)
        return _fallback_pypdf_to_blocks(file_path, doc_id=doc_id, source_path=source_path)

    strategy = "hi_res" if use_hi_res or infer_table_structure else "fast"
    lang_list = languages if languages is not None else ["chi_sim", "eng"]

    try:
        elements = partition_pdf(
            filename=file_path,
            strategy=strategy,
            infer_table_structure=infer_table_structure,
            languages=lang_list,
            ocr_languages=ocr_languages,
        )
    except Exception as e:
        logger.warning("partition_pdf failed (%s), fallback to pypdf", e)
        return _fallback_pypdf_to_blocks(file_path, doc_id=doc_id, source_path=source_path)

    if not elements:
        logger.info("partition_pdf returned 0 elements, fallback to pypdf")
        return _fallback_pypdf_to_blocks(file_path, doc_id=doc_id, source_path=source_path)

    blocks: List[Block] = []
    for el in elements:
        b = _map_unstructured_element_to_block(el, doc_id=doc_id, source_path=source_path)
        if b is not None:
            blocks.append(b)
    return blocks


def _docx_text(el: Any, ns: Dict[str, str]) -> str:
    parts = [node.text or "" for node in el.findall(".//w:t", ns)]
    return "".join(parts).strip()


def _docx_paragraph_style(paragraph: Any, ns: Dict[str, str]) -> Optional[str]:
    style = paragraph.find("./w:pPr/w:pStyle", ns)
    if style is None:
        return None
    return style.attrib.get(f"{{{ns['w']}}}val")


def _docx_is_list_item(paragraph: Any, ns: Dict[str, str]) -> bool:
    return paragraph.find("./w:pPr/w:numPr", ns) is not None


def _docx_table_to_markdown(table: Any, ns: Dict[str, str]) -> str:
    rows = []
    for row in table.findall("./w:tr", ns):
        cells = []
        for cell in row.findall("./w:tc", ns):
            cells.append(" ".join(_docx_text(cell, ns).split()))
        rows.append(cells)

    if not rows:
        return ""

    max_cols = max(len(row) for row in rows)
    normalized = [row + [""] * (max_cols - len(row)) for row in rows]
    header = normalized[0]
    separator = ["---"] * max_cols
    body = normalized[1:]

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def parse_docx_to_blocks(
    file_path: str,
    *,
    doc_id: str,
    source_path: str,
) -> List[Block]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)
    if _normalize_suffix(file_path) != ".docx":
        raise ValueError(f"Not a DOCX file: {file_path}")

    import zipfile
    import xml.etree.ElementTree as ET

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    with zipfile.ZipFile(file_path) as zf:
        xml_bytes = zf.read("word/document.xml")

    root = ET.fromstring(xml_bytes)
    body = root.find("w:body", ns)
    if body is None:
        return []

    blocks: List[Block] = []

    for item in list(body):
        if item.tag == f"{{{ns['w']}}}p":
            text = _docx_text(item, ns)
            if not text:
                continue

            style_name = _docx_paragraph_style(item, ns)
            style_key = (style_name or "").lower()
            if style_key.startswith("heading") or style_key == "title":
                block_type: BlockType = "title"
            elif _docx_is_list_item(item, ns) or "list" in style_key:
                block_type = "list_item"
            else:
                block_type = "text"

            blocks.append(
                Block(
                    block_type=block_type,
                    text=text,
                    page_no=None,
                    source_path=source_path,
                    doc_id=doc_id,
                    extra_info={"style": style_name},
                )
            )
            continue

        if item.tag == f"{{{ns['w']}}}tbl":
            markdown = _docx_table_to_markdown(item, ns).strip()
            if not markdown:
                continue
            blocks.append(
                Block(
                    block_type="table",
                    text=markdown,
                    page_no=None,
                    source_path=source_path,
                    doc_id=doc_id,
                    extra_info={"format": "markdown"},
                )
            )

    return blocks


class _HTMLBlockParser(HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header"}
    BLOCK_TAGS = {"p", "div", "section", "article", "main"}
    TITLE_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "title"}

    def __init__(self, *, doc_id: str, source_path: str):
        super().__init__(convert_charrefs=True)
        self.doc_id = doc_id
        self.source_path = source_path
        self.blocks: List[Block] = []
        self.stack: List[str] = []
        self.skip_depth = 0
        self.current_tag: Optional[str] = None
        self.current_text: List[str] = []
        self.current_link: Optional[str] = None
        self.table_rows: Optional[List[List[str]]] = None
        self.current_row: Optional[List[str]] = None
        self.current_cell: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        tag = tag.lower()
        self.stack.append(tag)
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return

        if tag == "table":
            self._flush_current()
            self.table_rows = []
            return
        if tag == "tr" and self.table_rows is not None:
            self.current_row = []
            return
        if tag in {"td", "th"} and self.current_row is not None:
            self.current_cell = []
            return

        if tag == "a":
            attrs_dict = dict(attrs)
            self.current_link = attrs_dict.get("href")

        if tag in self.TITLE_TAGS or tag in self.BLOCK_TAGS or tag == "li":
            self._start_text_block(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.stack:
            self.stack.pop()
        if tag in self.SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return

        if tag in {"td", "th"} and self.current_cell is not None:
            cell_text = self._normalize_text(" ".join(self.current_cell))
            if self.current_row is not None:
                self.current_row.append(cell_text)
            self.current_cell = None
            return
        if tag == "tr" and self.current_row is not None:
            if self.table_rows is not None and any(cell for cell in self.current_row):
                self.table_rows.append(self.current_row)
            self.current_row = None
            return
        if tag == "table":
            self._flush_table()
            return

        if tag == "a":
            self.current_link = None

        if tag == self.current_tag:
            self._flush_current()

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = html.unescape(data)
        if self.current_cell is not None:
            self.current_cell.append(text)
            return
        if self.current_tag is not None:
            if self.current_link and text.strip():
                self.current_text.append(f"{text} ({self.current_link})")
            else:
                self.current_text.append(text)

    def close(self) -> None:
        self._flush_current()
        self._flush_table()
        super().close()

    def _start_text_block(self, tag: str) -> None:
        if self.current_tag and self.current_tag != tag:
            self._flush_current()
        self.current_tag = tag
        self.current_text = []

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join(text.split()).strip()

    def _flush_current(self) -> None:
        if self.current_tag is None:
            return
        text = self._normalize_text(" ".join(self.current_text))
        tag = self.current_tag
        self.current_tag = None
        self.current_text = []
        if not text:
            return

        if tag in self.TITLE_TAGS:
            block_type: BlockType = "title"
        elif tag == "li":
            block_type = "list_item"
        else:
            block_type = "text"

        self.blocks.append(
            Block(
                block_type=block_type,
                text=text,
                page_no=None,
                source_path=self.source_path,
                doc_id=self.doc_id,
                extra_info={"tag": tag},
            )
        )

    def _flush_table(self) -> None:
        if self.table_rows is None:
            return
        rows = self.table_rows
        self.table_rows = None
        if not rows:
            return

        max_cols = max(len(row) for row in rows)
        normalized = [row + [""] * (max_cols - len(row)) for row in rows]
        header = normalized[0]
        separator = ["---"] * max_cols
        body = normalized[1:]
        lines = [
            "| " + " | ".join(header) + " |",
            "| " + " | ".join(separator) + " |",
        ]
        for row in body:
            lines.append("| " + " | ".join(row) + " |")
        markdown = "\n".join(lines).strip()
        if markdown:
            self.blocks.append(
                Block(
                    block_type="table",
                    text=markdown,
                    page_no=None,
                    source_path=self.source_path,
                    doc_id=self.doc_id,
                    extra_info={"format": "markdown", "tag": "table"},
                )
            )


def parse_html_to_blocks(
    file_path: str,
    *,
    doc_id: str,
    source_path: str,
) -> List[Block]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)
    if _normalize_suffix(file_path) not in (".html", ".htm"):
        raise ValueError(f"Not an HTML file: {file_path}")

    raw = _read_text_file(file_path)
    parser = _HTMLBlockParser(doc_id=doc_id, source_path=source_path)
    parser.feed(raw)
    parser.close()
    return parser.blocks


def _looks_like_text_heading(line: str) -> bool:
    line = line.strip()
    if not line:
        return False
    if line.startswith("#"):
        return True
    if len(line) > 80:
        return False
    patterns = (
        r"^第[一二三四五六七八九十百千万\d]+[章节篇部卷]\b",
        r"^\d+(\.\d+)*[、.．\s]+",
        r"^[一二三四五六七八九十]+[、.．]\s*",
    )
    return any(re.match(pattern, line) for pattern in patterns)


def _parse_yaml_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from text and return metadata dict and remaining content."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    
    metadata: dict = {}
    content_lines: list[str] = []
    in_frontmatter = True
    
    for i, line in enumerate(lines):
        if i == 0:
            continue
        if line.strip() == "---":
            in_frontmatter = False
            continue
        if in_frontmatter:
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()
        else:
            content_lines.append(line)
    
    return metadata, "\n".join(content_lines)


def parse_text_to_blocks(
    file_path: str,
    *,
    doc_id: str,
    source_path: str,
) -> List[Block]:
    text = _read_text_file(file_path).strip()
    if not text:
        return []

    frontmatter, content = _parse_yaml_frontmatter(text)
    
    blocks: List[Block] = []
    paragraph_lines: List[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        paragraph = "\n".join(paragraph_lines).strip()
        paragraph_lines = []
        if not paragraph:
            return
        blocks.append(
            Block(
                block_type="text",
                text=paragraph,
                page_no=None,
                source_path=source_path,
                doc_id=doc_id,
                extra_info=dict(frontmatter) if frontmatter else None,
            )
        )

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            continue

        if _looks_like_text_heading(line):
            flush_paragraph()
            blocks.append(
                Block(
                    block_type="title",
                    text=line.lstrip("#").strip(),
                    page_no=None,
                    source_path=source_path,
                    doc_id=doc_id,
                    extra_info=dict(frontmatter) if frontmatter else None,
                )
            )
            continue

        paragraph_lines.append(line)

    flush_paragraph()
    return blocks


def parse_file_to_blocks(
    file_path: str,
    *,
    doc_id: str,
    source_path: str,
    use_hi_res_pdf: bool = False,
    infer_table_structure_pdf: bool = False,
) -> List[Block]:
    suffix = _normalize_suffix(file_path)

    if suffix == ".pdf":
        return parse_pdf_to_blocks(
            file_path,
            doc_id=doc_id,
            source_path=source_path,
            use_hi_res=use_hi_res_pdf,
            infer_table_structure=infer_table_structure_pdf,
        )

    if suffix == ".docx":
        return parse_docx_to_blocks(
            file_path,
            doc_id=doc_id,
            source_path=source_path,
        )

    if suffix in (".html", ".htm"):
        return parse_html_to_blocks(
            file_path,
            doc_id=doc_id,
            source_path=source_path,
        )

    if suffix in (".txt", ".md", ".markdown"):
        return parse_text_to_blocks(
            file_path,
            doc_id=doc_id,
            source_path=source_path,
        )

    try:
        from unstructured.partition.auto import partition
    except ImportError as e:
        logger.warning("unstructured not available for auto partition: %s", e)
        text = _read_text_file(file_path).strip()
        return [
            Block(
                block_type="text",
                text=text,
                page_no=None,
                source_path=source_path,
                doc_id=doc_id,
            )
        ] if text else []

    try:
        elements = partition(filename=file_path)
    except Exception as e:
        logger.warning("partition(auto) failed (%s), fallback to raw text read", e)
        text = _read_text_file(file_path).strip()
        return [
            Block(
                block_type="text",
                text=text,
                page_no=None,
                source_path=source_path,
                doc_id=doc_id,
            )
        ] if text else []

    blocks: List[Block] = []
    for el in elements:
        b = _map_unstructured_element_to_block(el, doc_id=doc_id, source_path=source_path)
        if b is not None:
            blocks.append(b)
    return blocks



from collections import Counter
from dataclasses import replace
from typing import Iterable


def _iter_lines(text: str) -> Iterable[str]:
    for line in text.splitlines():
        line = line.strip()
        if line:
            yield line


def remove_repeated_headers_footers(
    blocks: List[Block],
    *,
    min_pages: int = 3,
    max_line_len: int = 40,
    min_repeat_ratio: float = 0.6,
) -> List[Block]:
    pages = sorted({b.page_no for b in blocks if b.page_no is not None})
    if len(pages) < min_pages:
        return blocks

    page_count = len(pages)

    # 统计“短行”跨页出现频次
    line_counter: Counter[str] = Counter()
    for b in blocks:
        if not b.text:
            continue
        for line in _iter_lines(b.text):
            if len(line) <= max_line_len:
                line_counter[line] += 1

    repeated_lines = {
        line
        for line, cnt in line_counter.items()
        if cnt / page_count >= min_repeat_ratio
    }

    if not repeated_lines:
        return blocks

    cleaned: List[Block] = []
    for b in blocks:
        if not b.text:
            cleaned.append(b)
            continue

        kept_lines = []
        removed_all = True

        for line in b.text.splitlines():
            raw = line
            line = line.strip()
            if not line:
                continue
            # 如果整行命中“跨页重复短行”，认为是页眉/页脚，丢弃
            if line in repeated_lines:
                continue
            removed_all = False
            kept_lines.append(raw)

        if removed_all:
            # 这个 block 全是页眉页脚，直接丢弃
            continue

        new_text = "\n".join(kept_lines).strip()
        if not new_text:
            continue

        cleaned.append(replace(b, text=new_text))

    return cleaned

import re
from dataclasses import replace
from typing import List, Optional


def _normalize_inline_spaces(text: str) -> str:
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_blocks(
    blocks: List[Block],
    *,
    short_title_max_len: int = 2,
    long_title_min_len: int = 40,
) -> List[Block]:
    if not blocks:
        return blocks

    normalized: List[Block] = []
    for b in blocks:
        if b.text:
            normalized.append(replace(b, text=_normalize_inline_spaces(b.text)))
        else:
            normalized.append(b)

    blocks = normalized

    # 1) 标题碎片合并：把连续的短 title（1~2字）拼到后一个 title 前面
    merged: List[Block] = []
    carry: Optional[str] = None

    for b in blocks:
        if b.block_type == "title":
            t = b.text.strip()
            if 0 < len(t) <= short_title_max_len:
                carry = (carry or "") + t
                continue

            if carry:
                t = carry + t
                carry = None

            merged.append(replace(b, text=t))
        else:
            if carry:
                # 如果短标题后面不是 title，把它降级成普通文本块塞回去
                merged.append(
                    Block(
                        block_type="text",
                        text=carry,
                        page_no=b.page_no,
                        source_path=b.source_path,
                        doc_id=b.doc_id,
                    )
                )
                carry = None
            merged.append(b)

    if carry:
        merged.append(
            Block(
                block_type="text",
                text=carry,
                page_no=merged[-1].page_no if merged else None,
                source_path=merged[-1].source_path if merged else "",
                doc_id=merged[-1].doc_id if merged else "",
            )
        )

    # 2) 长标题降级：过长 title 多半是正文误判，改为 text
    cleaned: List[Block] = []
    for b in merged:
        if b.block_type == "title" and len(b.text.strip()) >= long_title_min_len:
            cleaned.append(replace(b, block_type="text"))
        else:
            cleaned.append(b)

    cleaned = remove_repeated_headers_footers(cleaned)
    return cleaned


def _is_page_number_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    patterns = [
        r"^\d{1,4}$",
        r"^[-–—]\s*\d{1,4}\s*[-–—]$",
        r"^\d{1,4}\s*/\s*\d{1,4}$",
        r"^(page|p\.)\s*\d{1,4}$",
        r"^第\s*\d{1,4}\s*页$",
        r"^页码\s*\d{1,4}$",
    ]
    return any(re.match(pattern, t, flags=re.IGNORECASE) for pattern in patterns)


def _contains_private_use_char(text: str) -> bool:
    return any(0xE000 <= ord(ch) <= 0xF8FF for ch in text)


def _is_pdf_formula_fragment_noise(text: str) -> bool:
    t = text.strip()
    if not t or not _contains_private_use_char(t):
        return False

    cjk_count = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    if cjk_count > 0:
        return False

    alnum_count = sum(1 for ch in t if ch.isalnum())
    private_count = sum(1 for ch in t if 0xE000 <= ord(ch) <= 0xF8FF)
    visible_len = sum(1 for ch in t if not ch.isspace())

    if visible_len <= 4:
        return True
    if len(t) <= 120 and private_count >= 1 and alnum_count <= 30:
        return True
    return False


def _is_docx_toc_noise(block: Block, text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if block.block_type == "table":
        return False
    if t.lower() in {"目录", "contents", "table of contents"}:
        return True
    if len(t) <= 120 and re.search(r"\.{3,}\s*\d{1,4}$", t):
        return True
    return False


def _is_html_navigation_noise(block: Block, text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if block.block_type == "table":
        return False
    common_noise = {
        "home",
        "menu",
        "login",
        "logout",
        "register",
        "search",
        "back to top",
        "more",
        "首页",
        "菜单",
        "登录",
        "退出",
        "注册",
        "搜索",
        "返回顶部",
        "更多",
    }
    return len(t) <= 30 and t.lower() in common_noise


def clean_blocks_by_file_type(blocks: List[Block], file_type: str) -> List[Block]:
    """Apply conservative, file-type-specific cleanup before common cleanup."""
    if not blocks:
        return blocks

    suffix = (file_type or "").lower()
    filtered: List[Block] = []

    for b in blocks:
        text = _normalize_inline_spaces(b.text or "")
        if not text:
            continue

        drop = False
        if suffix == ".pdf":
            drop = _is_page_number_noise(text) or _is_pdf_formula_fragment_noise(text)
        elif suffix == ".docx":
            drop = _is_docx_toc_noise(b, text)
        elif suffix in {".html", ".htm"}:
            drop = _is_html_navigation_noise(b, text)

        if not drop:
            filtered.append(replace(b, text=text))

    if suffix in {".md", ".markdown", ".txt"}:
        return filtered

    return clean_blocks(filtered)


def _is_page_number_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    patterns = [
        r"^\d{1,4}$",
        r"^[-\u2013\u2014]\s*\d{1,4}\s*[-\u2013\u2014]$",
        r"^\d{1,4}\s*/\s*\d{1,4}$",
        r"^(page|p\.)\s*\d{1,4}$",
        r"^\u7b2c\s*\d{1,4}\s*\u9875$",
        r"^\u9875\u7801\s*\d{1,4}$",
    ]
    return any(re.match(pattern, t, flags=re.IGNORECASE) for pattern in patterns)


def _is_docx_toc_noise(block: Block, text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if block.block_type == "table":
        return False
    if t.lower() in {"\u76ee\u5f55", "contents", "table of contents"}:
        return True
    if len(t) <= 120 and re.search(r"\.{3,}\s*\d{1,4}$", t):
        return True
    return False


def _is_html_navigation_noise(block: Block, text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if block.block_type == "table":
        return False
    common_noise = {
        "home",
        "menu",
        "login",
        "logout",
        "register",
        "search",
        "back to top",
        "more",
        "\u9996\u9875",
        "\u83dc\u5355",
        "\u767b\u5f55",
        "\u9000\u51fa",
        "\u6ce8\u518c",
        "\u641c\u7d22",
        "\u8fd4\u56de\u9876\u90e8",
        "\u66f4\u591a",
    }
    return len(t) <= 30 and t.lower() in common_noise

from dataclasses import replace
from typing import List, Optional, Tuple


def _get_bbox_xywh(b: Block) -> Optional[Tuple[float, float, float, float]]:
    if not b.extra_info:
        return None
    c = b.extra_info.get("coordinates")
    if not c:
        return None
    points = getattr(c, "points", None) or c.get("points")
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return x0, y0, (x1 - x0), (y1 - y0)


def _looks_like_sentence(t: str) -> bool:
    if len(t) >= 40:
        return True
    if any(p in t for p in ("。", "；", "，", ",")):
        return True
    return False


def fix_titles(
    blocks: List[Block],
    *,
    short_title_max_len: int = 2,
    long_title_min_len: int = 40,
    same_indent_tol: float = 12.0,
    vertical_gap_tol: float = 18.0,
) -> List[Block]:
    if not blocks:
        return blocks

    out: List[Block] = []
    buf: List[Block] = []

    def flush_buf() -> None:
        nonlocal buf, out
        if not buf:
            return
        text = "".join([b.text.strip() for b in buf]).strip()
        b0 = buf[0]
        merged = replace(b0, text=text)
        out.append(merged)
        buf = []

    def can_merge(prev: Block, curr: Block) -> bool:
        if prev.page_no != curr.page_no:
            return False
        if prev.block_type != "title" or curr.block_type != "title":
            return False
        prev_t = prev.text.strip()
        curr_t = curr.text.strip()
        if not prev_t or not curr_t:
            return False

        prev_bbox = _get_bbox_xywh(prev)
        curr_bbox = _get_bbox_xywh(curr)
        if prev_bbox and curr_bbox:
            px, py, pw, ph = prev_bbox
            cx, cy, cw, ch = curr_bbox
            same_indent = abs(px - cx) <= same_indent_tol
            vertical_gap = abs((py + ph) - cy) <= vertical_gap_tol
            return same_indent and vertical_gap

        return len(prev_t) <= short_title_max_len or len(curr_t) <= short_title_max_len

    for b in blocks:
        if b.block_type != "title":
            flush_buf()
            out.append(b)
            continue

        if not buf:
            buf.append(b)
            continue

        if can_merge(buf[-1], b):
            buf.append(b)
        else:
            flush_buf()
            buf.append(b)

    flush_buf()

    final: List[Block] = []
    for b in out:
        if b.block_type == "title":
            t = b.text.strip()
            if len(t) >= long_title_min_len or _looks_like_sentence(t):
                final.append(replace(b, block_type="text"))
            else:
                final.append(b)
        else:
            final.append(b)

    return final

def _block_to_section_text(block: Block) -> str:
    if block.block_type == "table":
        table_text = (block.html or block.text or "").strip()
        extra = dict(block.extra_info or {})
        table_id = str(extra.get("table_id") or "").strip()
        if table_id:
            page_range = str(extra.get("page_range") or "").strip()
            headers = extra.get("headers") or []
            header_text = ", ".join(str(item).strip() for item in headers if str(item).strip())
            metadata_lines = [f"[TABLE_ID: {table_id}]"]
            if page_range:
                metadata_lines.append(f"[TABLE_PAGE_RANGE: {page_range}]")
            if header_text:
                metadata_lines.append(f"[TABLE_HEADERS: {header_text}]")
            return "\n".join(metadata_lines + [table_text]).strip()
        return table_text
    if block.block_type == "title":
        return f"## {block.text.strip()}"
    if block.block_type == "list_item":
        return f"- {block.text.strip()}"
    return (block.text or "").strip()


def _parse_markdown_table_rows(text: str) -> List[List[str]]:
    rows: List[List[str]] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells):
            continue
        if any(cells):
            rows.append(cells)
    return rows


def _normalize_table_header(row: List[str]) -> List[str]:
    return [re.sub(r"\s+", " ", cell).strip().lower() for cell in row]


def _table_column_count(block: Block) -> int:
    rows = _parse_markdown_table_rows(block.text)
    if rows:
        return max(len(row) for row in rows)
    text_lines = [line for line in (block.text or "").splitlines() if line.strip()]
    return max((len(re.split(r"\s{2,}|\t+", line.strip())) for line in text_lines), default=0)


def _table_header(block: Block) -> List[str]:
    rows = _parse_markdown_table_rows(block.text)
    return _normalize_table_header(rows[0]) if rows else []


def _looks_like_continued_table(left: Block, right: Block) -> bool:
    if left.block_type != "table" or right.block_type != "table":
        return False
    if left.doc_id != right.doc_id or left.source_path != right.source_path:
        return False
    if left.page_no is None or right.page_no is None or right.page_no != left.page_no + 1:
        return False

    left_cols = _table_column_count(left)
    right_cols = _table_column_count(right)
    if left_cols <= 0 or right_cols <= 0 or left_cols != right_cols:
        return False

    left_header = _table_header(left)
    right_header = _table_header(right)
    if left_header and right_header and left_header == right_header:
        return True

    right_text = (right.text or "").strip().lower()
    if right_text.startswith(("continued", "cont.", "续表")):
        return True

    return bool(left_header and not right_header)


def _markdown_table_from_rows(rows: List[List[str]]) -> str:
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


def _merge_table_blocks(table_blocks: List[Block], table_id: str) -> Block:
    first = table_blocks[0]
    rows: List[List[str]] = []
    first_header: List[str] = []
    all_markdown = True

    for block in table_blocks:
        block_rows = _parse_markdown_table_rows(block.text)
        if not block_rows:
            all_markdown = False
            break
        if not rows:
            rows.extend(block_rows)
            first_header = _normalize_table_header(block_rows[0])
            continue
        next_header = _normalize_table_header(block_rows[0])
        rows.extend(block_rows[1:] if first_header and next_header == first_header else block_rows)

    if all_markdown:
        merged_text = _markdown_table_from_rows(rows)
        headers = rows[0] if rows else []
        row_count = max(len(rows) - 1, 0)
    else:
        merged_text = "\n\n".join((block.text or block.html or "").strip() for block in table_blocks if (block.text or block.html))
        headers = _table_header(first)
        row_count = max(len(_parse_markdown_table_rows(merged_text)) - 1, 0)

    page_numbers = [block.page_no for block in table_blocks if block.page_no is not None]
    page_range = _format_page_range(page_numbers)
    extra_info = dict(first.extra_info or {})
    extra_info.update(
        {
            "table_id": table_id,
            "is_cross_page_table": len(set(page_numbers)) > 1,
            "page_range": page_range,
            "source_pages": sorted(set(page_numbers)),
            "headers": headers,
            "row_count": row_count,
            "table_part_count": len(table_blocks),
        }
    )
    return replace(
        first,
        text=merged_text.strip(),
        html=None if all_markdown else first.html,
        extra_info=extra_info,
    )


def merge_cross_page_tables(blocks: List[Block]) -> List[Block]:
    """Merge conservative cross-page table continuations before section chunking."""
    if not blocks:
        return blocks

    merged: List[Block] = []
    pending: List[Block] = []
    table_counter_by_doc: Dict[str, int] = {}

    def flush_pending() -> None:
        nonlocal pending
        if not pending:
            return
        doc_id = pending[0].doc_id or "unknown_doc"
        idx = table_counter_by_doc.get(doc_id, 0)
        table_counter_by_doc[doc_id] = idx + 1
        table_id = f"{doc_id}::table::{idx}"
        merged.append(_merge_table_blocks(pending, table_id))
        pending = []

    for block in blocks:
        if block.block_type != "table":
            flush_pending()
            merged.append(block)
            continue
        if not pending:
            pending.append(block)
            continue
        if _looks_like_continued_table(pending[-1], block):
            pending.append(block)
        else:
            flush_pending()
            pending.append(block)

    flush_pending()
    return merged


def split_large_tables(blocks: List[Block], max_table_chars: int = 3000) -> List[Block]:
    """Split long Markdown tables by rows while repeating the header in each part."""
    if max_table_chars <= 0 or not blocks:
        return blocks

    out: List[Block] = []
    for block in blocks:
        if block.block_type != "table" or len((block.text or "").strip()) <= max_table_chars:
            out.append(block)
            continue

        rows = _parse_markdown_table_rows(block.text)
        if len(rows) <= 2:
            out.append(block)
            continue

        header = rows[0]
        body_rows = rows[1:]
        parts: List[List[List[str]]] = []
        current_rows: List[List[str]] = []

        for row in body_rows:
            candidate = [header] + current_rows + [row]
            candidate_text = _markdown_table_from_rows(candidate)
            if current_rows and len(candidate_text) > max_table_chars:
                parts.append([header] + current_rows)
                current_rows = [row]
            else:
                current_rows.append(row)

        if current_rows:
            parts.append([header] + current_rows)

        if len(parts) <= 1:
            out.append(block)
            continue

        base_extra = dict(block.extra_info or {})
        table_id = str(base_extra.get("table_id") or "").strip()
        for part_index, part_rows in enumerate(parts):
            part_extra = dict(base_extra)
            part_extra.update(
                {
                    "table_id": table_id,
                    "table_part_index": part_index,
                    "table_part_count": len(parts),
                    "row_count": max(len(part_rows) - 1, 0),
                    "headers": part_rows[0] if part_rows else base_extra.get("headers", []),
                }
            )
            out.append(
                replace(
                    block,
                    text=_markdown_table_from_rows(part_rows),
                    html=None,
                    extra_info=part_extra,
                )
            )

    return out


def _parse_page_range(page_range: str) -> List[int]:
    if not page_range:
        return []
    try:
        if "-" in page_range:
            start, end = page_range.split("-", 1)
            return [int(start), int(end)]
        return [int(page_range)]
    except ValueError:
        return []


def _format_page_range(page_numbers: List[int]) -> str:
    page_numbers = sorted(set(page_numbers))
    if not page_numbers:
        return ""
    if page_numbers[0] == page_numbers[-1]:
        return str(page_numbers[0])
    return f"{page_numbers[0]}-{page_numbers[-1]}"


def _build_section_id(doc_id: str, section_index: int, block_start: int, block_end: int) -> str:
    safe_doc_id = doc_id or "unknown_doc"
    return f"{safe_doc_id}::section::{section_index}::blocks::{block_start}-{block_end}"


def _can_merge_sections(left: Document, right: Document) -> bool:
    return (
        left.metadata.get("doc_id") == right.metadata.get("doc_id")
        and left.metadata.get("source_path") == right.metadata.get("source_path")
    )


def _is_strong_section_title(title: str) -> bool:
    title = (title or "").strip()
    if not title:
        return False
    strong_prefixes = ("第", "§", "Chapter", "CHAPTER", "Part", "PART")
    if title.startswith(strong_prefixes):
        return True
    return bool(re.match(r"^\d+\s+", title))


def _can_merge_short_sections(left: Document, right: Document) -> bool:
    if not _can_merge_sections(left, right):
        return False

    left_title = str(left.metadata.get("section_title") or "")
    right_title = str(right.metadata.get("section_title") or "")

    # Keep strong chapter/section boundaries. Parent sections should be larger,
    # but not at the cost of mixing two clearly independent topics.
    if _is_strong_section_title(right_title) and left_title and right_title != left_title:
        return False

    return True


def _is_strong_section_title(title: str) -> bool:
    title = (title or "").strip()
    if not title:
        return False
    strong_prefixes = ("第", "§", "Chapter", "CHAPTER", "Part", "PART")
    if title.startswith(strong_prefixes):
        return True
    return bool(re.match(r"^\d+\s+", title))


def _section_body_length(doc: Document) -> int:
    text = (doc.text or "").strip()
    title = str(doc.metadata.get("section_title") or "").strip()
    if title:
        text = text.replace(f"## {title}", "", 1).strip()
        text = text.replace(title, "", 1).strip()
    return len(text)


def _can_merge_short_sections(
    left: Document,
    right: Document,
    *,
    title_only_body_threshold: int = 120,
) -> bool:
    if not _can_merge_sections(left, right):
        return False

    left_title = str(left.metadata.get("section_title") or "")
    right_title = str(right.metadata.get("section_title") or "")
    left_body_len = _section_body_length(left)
    right_body_len = _section_body_length(right)

    # Strong headings are boundaries only when both sides already carry real
    # body content. A heading-only section should merge forward into its body.
    if (
        _is_strong_section_title(right_title)
        and left_title
        and right_title != left_title
        and left_body_len > title_only_body_threshold
        and right_body_len > title_only_body_threshold
    ):
        return False

    return True


def _merge_section_documents(left: Document, right: Document) -> Document:
    left_text = (left.text or "").strip()
    right_text = (right.text or "").strip()
    merged_text = "\n\n".join(part for part in [left_text, right_text] if part).strip()

    left_meta = dict(left.metadata)
    right_meta = dict(right.metadata)
    page_numbers = _parse_page_range(left_meta.get("page_range") or "") + _parse_page_range(right_meta.get("page_range") or "")
    block_start = min(
        left_meta.get("block_start_index", left_meta.get("block_index", 0)),
        right_meta.get("block_start_index", right_meta.get("block_index", 0)),
    )
    block_end = max(
        left_meta.get("block_end_index", left_meta.get("block_index", 0)),
        right_meta.get("block_end_index", right_meta.get("block_index", 0)),
    )

    element_type = left_meta.get("element_type") or right_meta.get("element_type") or "mixed"
    if element_type == "title":
        element_type = right_meta.get("element_type") or "text"

    section_title = left_meta.get("section_title") or right_meta.get("section_title") or ""
    metadata = {
        "doc_id": left_meta.get("doc_id") or right_meta.get("doc_id"),
        "section_id": left_meta.get("section_id") or right_meta.get("section_id"),
        "section_index": left_meta.get("section_index", 0),
        "section_title": section_title,
        "section_path": section_title,
        "source_path": left_meta.get("source_path") or right_meta.get("source_path"),
        "page_range": _format_page_range(page_numbers),
        "element_type": element_type,
        "block_index": block_start,
        "block_start_index": block_start,
        "block_end_index": block_end,
        "block_count": block_end - block_start + 1,
    }
    return Document(text=merged_text, metadata=metadata)


def _reindex_section_documents(docs: List[Document]) -> List[Document]:
    counters: Dict[str, int] = {}
    out: List[Document] = []
    for doc in docs:
        metadata = dict(doc.metadata)
        doc_id = metadata.get("doc_id") or "unknown_doc"
        idx = counters.get(doc_id, 0)
        counters[doc_id] = idx + 1
        block_start = metadata.get("block_start_index", metadata.get("block_index", 0))
        block_end = metadata.get("block_end_index", block_start)
        section_id = _build_section_id(doc_id, idx, block_start, block_end)
        metadata["section_index"] = idx
        metadata["section_id"] = section_id
        out.append(Document(text=doc.text or "", metadata=metadata))
    return out


def _merge_short_section_documents(
    docs: List[Document],
    min_section_chars: int,
) -> List[Document]:
    if min_section_chars <= 0 or len(docs) <= 1:
        return _reindex_section_documents(docs)

    merged = list(docs)
    for _ in range(5):
        changed = False
        out: List[Document] = []
        i = 0
        while i < len(merged):
            current = merged[i]
            current_len = len((current.text or "").strip())

            if current_len < min_section_chars and i + 1 < len(merged) and _can_merge_short_sections(current, merged[i + 1]):
                out.append(_merge_section_documents(current, merged[i + 1]))
                i += 2
                changed = True
                continue

            if current_len < min_section_chars and out and _can_merge_short_sections(out[-1], current):
                out[-1] = _merge_section_documents(out[-1], current)
                i += 1
                changed = True
                continue

            out.append(current)
            i += 1

        merged = out
        if not changed:
            break

    return _reindex_section_documents(merged)


def blocks_to_llama_documents(
    blocks: List[Block],
    max_section_chars: int = 4000,
    min_section_chars: int = 800,
) -> List[Document]:
    """Merge parsed blocks into section-level documents before chunking.

    The splitter should receive meaningful sections instead of one tiny document
    per parser block. This keeps titles, paragraphs, lists, and tables together
    while still allowing hierarchical chunking to split long documents later.
    """
    docs: List[Document] = []
    section_parts: List[str] = []
    section_block_ids: List[str] = []
    section_element_types: List[str] = []
    section_page_numbers: List[int] = []
    section_title = ""
    section_index = 0
    section_source_path = ""
    section_doc_id = ""
    section_start_block_index = 0
    section_frontmatter: dict = {}

    def flush_section() -> None:
        nonlocal section_index

        text = "\n\n".join(part for part in section_parts if part.strip()).strip()
        if not text:
            return

        page_numbers = sorted(set(section_page_numbers))
        element_type_counts: Dict[str, int] = {}
        for element_type in section_element_types:
            element_type_counts[element_type] = element_type_counts.get(element_type, 0) + 1
        main_element_type = max(
            element_type_counts,
            key=lambda key: (element_type_counts[key], key != "title"),
            default="mixed",
        )

        source_path = section_source_path or (blocks[0].source_path if blocks else "")
        doc_id = section_doc_id or (blocks[0].doc_id if blocks else "")
        block_start = section_start_block_index
        block_end = section_start_block_index + len(section_block_ids) - 1
        section_id = _build_section_id(doc_id, section_index, block_start, block_end)
        if not page_numbers:
            page_range = ""
        elif page_numbers[0] == page_numbers[-1]:
            page_range = str(page_numbers[0])
        else:
            page_range = f"{page_numbers[0]}-{page_numbers[-1]}"

        metadata = {
            "doc_id": doc_id,
            "section_id": section_id,
            "section_index": section_index,
            "section_title": section_title,
            "section_path": section_title,
            "source_path": source_path,
            "page_range": page_range,
            "element_type": main_element_type,
            "block_index": block_start,
            "block_start_index": block_start,
            "block_end_index": block_end,
            "block_count": len(section_block_ids),
        }
        
        if section_frontmatter:
            metadata.update(section_frontmatter)

        docs.append(
            Document(
                text=text,
                metadata=metadata,
            )
        )
        section_index += 1

    def reset_section(title: str, source_path: str, doc_id: str, block_index: int, frontmatter: dict = {}) -> None:
        section_parts.clear()
        section_block_ids.clear()
        section_element_types.clear()
        section_page_numbers.clear()
        nonlocal section_title, section_source_path, section_doc_id, section_start_block_index, section_frontmatter
        section_title = title
        section_source_path = source_path
        section_doc_id = doc_id
        section_start_block_index = block_index
        section_frontmatter = frontmatter

    for i, b in enumerate(blocks):
        if not b.text and not b.html:
            continue

        block_id = f"{b.doc_id}::block::{i}"
        content = _block_to_section_text(b)
        if not content:
            continue

        current_length = sum(len(part) for part in section_parts)
        should_start_new_section = b.block_type == "title" and section_parts
        should_flush_by_size = (
            b.block_type != "title"
            and section_parts
            and current_length + len(content) > max_section_chars
        )

        block_frontmatter = dict(b.extra_info) if b.extra_info else {}

        if should_start_new_section or should_flush_by_size:
            flush_section()
            reset_section(
                b.text.strip() if b.block_type == "title" else section_title,
                b.source_path,
                b.doc_id,
                i,
                block_frontmatter,
            )
        elif not section_parts:
            reset_section(
                b.text.strip() if b.block_type == "title" else "",
                b.source_path,
                b.doc_id,
                i,
                block_frontmatter,
            )

        if b.block_type == "title":
            section_title = b.text.strip()

        section_parts.append(content)
        section_block_ids.append(block_id)
        section_element_types.append(b.block_type)
        if b.page_no is not None:
            section_page_numbers.append(b.page_no)

    flush_section()
    return _merge_short_section_documents(docs, min_section_chars)


def blocks_to_markdown(blocks: List[Block]) -> str:
    out: List[str] = []
    last_page: Optional[int] = None

    for i, b in enumerate(blocks):
        if b.page_no is not None and b.page_no != last_page:
            out.append(f"\n---\n\n## Page {b.page_no}\n")
            last_page = b.page_no

        out.append(f"<!-- block_index={i} block_type={b.block_type} -->\n")

        if b.block_type == "title":
            out.append(f"## {b.text}\n")
        elif b.block_type == "list_item":
            out.append(f"- {b.text}\n")
        elif b.block_type == "table":
            out.append((b.html or b.text) + "\n")
        else:
            out.append(b.text + "\n")

    return "".join(out).strip() + "\n"


def analyze_block_quality(
    blocks: List[Block],
    *,
    file_type: str = "",
    pdf_probe: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build lightweight parsing quality signals for ingestion reports.

    The signals are intentionally heuristic. They help reviewers quickly find
    documents that need OCR, table-aware parsing, or metadata cleanup, without
    changing the actual indexing output.
    """

    block_type_counts: Dict[str, int] = {}
    page_numbers = sorted({b.page_no for b in blocks if b.page_no is not None})
    text_lengths = [len((b.text or "").strip()) for b in blocks if (b.text or "").strip()]
    missing_page_count = sum(1 for b in blocks if b.page_no is None)
    table_blocks = [b for b in blocks if b.block_type == "table"]
    image_blocks = [b for b in blocks if b.block_type == "image"]

    for block in blocks:
        block_type_counts[block.block_type] = block_type_counts.get(block.block_type, 0) + 1

    cross_page_table_candidates = 0
    for left, right in zip(table_blocks, table_blocks[1:]):
        if left.page_no is not None and right.page_no is not None and right.page_no == left.page_no + 1:
            cross_page_table_candidates += 1

    flags: List[str] = []
    probe = pdf_probe or {}
    if probe.get("ocr_required"):
        flags.append("scanned_pdf_requires_ocr")
    if table_blocks:
        flags.append("table_blocks_present")
    if cross_page_table_candidates:
        flags.append("cross_page_table_candidate")
    if image_blocks:
        flags.append("image_blocks_present")
    if blocks and missing_page_count / len(blocks) >= 0.5:
        flags.append("missing_page_metadata")
    if not blocks:
        flags.append("empty_parse_result")
    elif text_lengths and sum(text_lengths) < 200:
        flags.append("very_sparse_text")

    quality_level = "ok"
    if "empty_parse_result" in flags or probe.get("ocr_required"):
        quality_level = "poor"
    elif flags:
        quality_level = "needs_review"

    avg_text_length = round(sum(text_lengths) / len(text_lengths), 2) if text_lengths else 0
    return {
        "parse_quality_level": quality_level,
        "parse_quality_flags": flags,
        "parse_block_type_counts": block_type_counts,
        "parse_page_count": len(page_numbers),
        "parse_page_range": _format_page_range(page_numbers),
        "parse_missing_page_block_count": missing_page_count,
        "parse_table_block_count": len(table_blocks),
        "parse_cross_page_table_candidate_count": cross_page_table_candidates,
        "parse_image_block_count": len(image_blocks),
        "parse_avg_block_text_length": avg_text_length,
        "parse_file_type": file_type,
    }


def section_documents_to_markdown(documents: List[Document]) -> str:
    out: List[str] = []
    for i, doc in enumerate(documents):
        metadata = doc.metadata or {}
        text = (doc.text or "").strip()
        out.append(f"## Section {i}\n\n")
        out.append("```text\n")
        out.append(f"section_id: {metadata.get('section_id', '')}\n")
        out.append(f"section_title: {metadata.get('section_title', '')}\n")
        out.append(f"source_path: {metadata.get('source_path', '')}\n")
        out.append(f"page_range: {metadata.get('page_range', '')}\n")
        out.append(f"element_type: {metadata.get('element_type', '')}\n")
        out.append(f"block_range: {metadata.get('block_start_index', '')}-{metadata.get('block_end_index', '')}\n")
        out.append(f"block_count: {metadata.get('block_count', '')}\n")
        out.append(f"length: {len(text)}\n")
        out.append("```\n\n")
        out.append(text)
        out.append("\n\n---\n\n")
    return "".join(out).strip() + "\n"

import os
import re


def _safe_filename(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[<>:\"/\\|?*\x00-\x1F]", "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:120] if len(name) > 120 else name


def save_markdown_audit(
    markdown_text: str,
    *,
    out_dir: str,
    doc_id: str,
    source_path: str,
) -> str:
    os.makedirs(out_dir, exist_ok=True)

    base = _safe_filename(doc_id or os.path.basename(source_path) or "doc")
    path = os.path.join(out_dir, f"{base}.md")

    header = f"---\ndoc_id: {doc_id}\nsource_path: {source_path}\n---\n\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(markdown_text)

    return path


def save_section_markdown_audit(
    documents: List[Document],
    *,
    out_dir: str,
    doc_id: str,
    source_path: str,
) -> str:
    markdown_text = section_documents_to_markdown(documents)
    return save_markdown_audit(
        markdown_text,
        out_dir=out_dir,
        doc_id=doc_id,
        source_path=source_path,
    )
    
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=False, default=None)
    parser.add_argument("--doc-id", default="test")
    parser.add_argument("--source-path", default=None)
    parser.add_argument("--pdf-hi-res", action="store_true")
    parser.add_argument("--pdf-tables", action="store_true")
    args = parser.parse_args()

    if args.file is None:
        from app.config import settings
        file_path = settings.test_pdf_path
        doc_id = settings.test_pdf_name
        source_path = settings.test_pdf_path
    else:
        file_path = args.file
        doc_id = args.doc_id
        source_path = args.source_path or os.path.basename(file_path)

    blocks = parse_file_to_blocks(
        file_path,
        doc_id=doc_id,
        source_path=source_path,
        use_hi_res_pdf=args.pdf_hi_res,
        infer_table_structure_pdf=args.pdf_tables,
    )
    blocks = clean_blocks(blocks)
    blocks = fix_titles(blocks)
    docs = blocks_to_llama_documents(blocks)
    md = blocks_to_markdown(blocks)
    save_markdown_audit(md, out_dir=settings.audit_dir, doc_id=doc_id, source_path=source_path)
    
    print("blocks =", len(blocks))    
    print("docs   =", len(docs))
    if docs:
        print("doc0.metadata =", docs[0].metadata)
        print("doc0.text[:160] =", docs[0].text[:160].replace("\n", " "))
