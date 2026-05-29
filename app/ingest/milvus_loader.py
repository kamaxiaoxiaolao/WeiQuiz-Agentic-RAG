import json
import os
import sys
from datetime import datetime, timezone
from typing import List, Dict, Optional

# 强制终端使用 UTF-8 输出，修复中文乱码
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

from llama_index.vector_stores.milvus import MilvusVectorStore
from llama_index.core.readers import SimpleDirectoryReader
from llama_index.core.schema import Document, TextNode, NodeRelationship, RelatedNodeInfo
from llama_index.core.node_parser import HierarchicalNodeParser, get_leaf_nodes, get_root_nodes
from llama_index.core import StorageContext, VectorStoreIndex, load_index_from_storage
from llama_index.core.settings import Settings
from llama_index.llms.openai_like import OpenAILike
from llama_index.embeddings.openai import OpenAIEmbedding

from app.config import settings as app_settings
from app.ingest.sync import load_state, save_state, diff_docs
from app.metadata_schema import build_hierarchy_node_metadata

try:
    from app.storage.parent_store import build_parent_store
except Exception:
    build_parent_store = None

try:
    from app.ingest.document_parser import (
        parse_file_to_blocks,
        blocks_to_llama_documents,
        blocks_to_markdown,
        save_section_markdown_audit,
        save_markdown_audit,
        clean_blocks,
        clean_blocks_by_file_type,
        fix_titles,
        probe_pdf_text_layer,
        analyze_block_quality,
    )
except Exception:
    parse_file_to_blocks = None
    blocks_to_llama_documents = None
    blocks_to_markdown = None
    save_section_markdown_audit = None
    save_markdown_audit = None
    clean_blocks = None
    clean_blocks_by_file_type = None
    fix_titles = None
    probe_pdf_text_layer = None
    analyze_block_quality = None


def _normalize_rel_path(raw_path: str, docs_dir: str) -> str:
    if not raw_path:
        return ""

    abs_raw = os.path.abspath(raw_path)
    abs_docs = os.path.abspath(docs_dir)

    if abs_raw.startswith(abs_docs):
        rel_path = os.path.relpath(abs_raw, abs_docs)
    else:
        rel_path = os.path.normpath(raw_path)

    return rel_path.replace("\\", "/")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _new_ingestion_report(diff_dict: Dict[str, List[dict]]) -> dict:
    started_at = _utc_now_iso()
    return {
        "started_at": started_at,
        "finished_at": None,
        "summary": {
            "added": len(diff_dict.get("added", [])),
            "updated": len(diff_dict.get("updated", [])),
            "deleted": len(diff_dict.get("deleted", [])),
            "succeeded": 0,
            "failed": 0,
        },
        "section_stats": {},
        "hierarchy_stats": {},
        "chunk_stats": {},
        "documents": [],
    }


def _percentile(sorted_values: List[int], percentile: float) -> int:
    if not sorted_values:
        return 0

    if len(sorted_values) == 1:
        return sorted_values[0]

    index = round((len(sorted_values) - 1) * percentile)
    return sorted_values[index]


def _build_chunk_stats(
    nodes: List[TextNode],
    path_to_file_type: Optional[Dict[str, str]] = None,
    *,
    short_threshold: int = 50,
    long_threshold: int = 1500,
) -> dict:
    path_to_file_type = path_to_file_type or {}
    lengths = sorted(len((node.text or "").strip()) for node in nodes)
    chunks_by_doc: Dict[str, int] = {}
    chunks_by_file_type: Dict[str, int] = {}

    for node in nodes:
        doc_id = node.metadata.get("doc_id") or "unknown_doc"
        source_path = _normalize_rel_path(node.metadata.get("source_path") or "", app_settings.docs_dir)
        file_type = path_to_file_type.get(source_path) or node.metadata.get("file_type") or "unknown"

        chunks_by_doc[doc_id] = chunks_by_doc.get(doc_id, 0) + 1
        chunks_by_file_type[file_type] = chunks_by_file_type.get(file_type, 0) + 1

    total_chunks = len(lengths)
    total_length = sum(lengths)

    return {
        "length_unit": "characters",
        "short_threshold": short_threshold,
        "long_threshold": long_threshold,
        "total_chunks": total_chunks,
        "avg_chunk_length": round(total_length / total_chunks, 2) if total_chunks else 0,
        "p50_chunk_length": _percentile(lengths, 0.50),
        "p90_chunk_length": _percentile(lengths, 0.90),
        "p95_chunk_length": _percentile(lengths, 0.95),
        "min_chunk_length": lengths[0] if lengths else 0,
        "max_chunk_length": lengths[-1] if lengths else 0,
        "short_chunk_count": sum(1 for value in lengths if value < short_threshold),
        "long_chunk_count": sum(1 for value in lengths if value > long_threshold),
        "chunks_by_doc": chunks_by_doc,
        "chunks_by_file_type": chunks_by_file_type,
    }


def _build_section_stats(
    documents: List[Document],
    *,
    short_threshold: int = 800,
    long_threshold: int = 4000,
) -> dict:
    lengths = sorted(len((doc.text or "").strip()) for doc in documents)
    sections_by_doc: Dict[str, int] = {}

    for doc in documents:
        doc_id = doc.metadata.get("doc_id") or "unknown_doc"
        sections_by_doc[doc_id] = sections_by_doc.get(doc_id, 0) + 1

    total_sections = len(lengths)
    total_length = sum(lengths)

    return {
        "length_unit": "characters",
        "short_threshold": short_threshold,
        "long_threshold": long_threshold,
        "total_sections": total_sections,
        "avg_section_length": round(total_length / total_sections, 2) if total_sections else 0,
        "p50_section_length": _percentile(lengths, 0.50),
        "p90_section_length": _percentile(lengths, 0.90),
        "p95_section_length": _percentile(lengths, 0.95),
        "min_section_length": lengths[0] if lengths else 0,
        "max_section_length": lengths[-1] if lengths else 0,
        "short_section_count": sum(1 for value in lengths if value < short_threshold),
        "long_section_count": sum(1 for value in lengths if value > long_threshold),
        "sections_by_doc": sections_by_doc,
    }


def _length_stats(lengths: List[int]) -> dict:
    values = sorted(lengths)
    total = len(values)
    total_length = sum(values)
    return {
        "count": total,
        "avg_length": round(total_length / total, 2) if total else 0,
        "p50_length": _percentile(values, 0.50),
        "p90_length": _percentile(values, 0.90),
        "p95_length": _percentile(values, 0.95),
        "min_length": values[0] if values else 0,
        "max_length": values[-1] if values else 0,
    }


def _build_hierarchy_stats(nodes: List[TextNode]) -> dict:
    role_lengths: Dict[str, List[int]] = {"root": [], "parent": [], "leaf": []}
    nodes_by_doc: Dict[str, Dict[str, int]] = {}

    for node in nodes:
        role = node.metadata.get("chunk_role") or "unknown"
        doc_id = node.metadata.get("doc_id") or "unknown_doc"
        length = len((node.text or "").strip())
        role_lengths.setdefault(role, []).append(length)
        nodes_by_doc.setdefault(doc_id, {})
        nodes_by_doc[doc_id][role] = nodes_by_doc[doc_id].get(role, 0) + 1

    return {
        "length_unit": "characters",
        "root": _length_stats(role_lengths.get("root", [])),
        "parent": _length_stats(role_lengths.get("parent", [])),
        "leaf": _length_stats(role_lengths.get("leaf", [])),
        "nodes_by_doc": nodes_by_doc,
    }


def _metadata_quality_fields(metadata: dict) -> dict:
    keys = (
        "pdf_probe_pages",
        "pdf_total_pages",
        "pdf_text_probe_chars",
        "pdf_scanned_threshold_chars",
        "is_scanned_pdf",
        "ocr_required",
        "ocr_status",
        "parse_quality_level",
        "parse_quality_flags",
        "parse_block_type_counts",
        "parse_page_count",
        "parse_page_range",
        "parse_missing_page_block_count",
        "parse_table_block_count",
        "parse_cross_page_table_candidate_count",
        "parse_image_block_count",
        "parse_avg_block_text_length",
        "parse_file_type",
    )
    return {key: metadata[key] for key in keys if key in metadata}


def _build_doc_quality_map(documents: List[Document]) -> Dict[str, dict]:
    quality_by_doc: Dict[str, dict] = {}
    for doc in documents:
        doc_id = doc.metadata.get("doc_id")
        if not doc_id or doc_id in quality_by_doc:
            continue
        quality = _metadata_quality_fields(doc.metadata)
        if quality:
            quality_by_doc[doc_id] = quality
    return quality_by_doc


class IngestionStageError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def _report_item(
    item: dict,
    action: str,
    status: str,
    *,
    stage: Optional[str] = None,
    block_count: Optional[int] = None,
    chunk_count: Optional[int] = None,
    quality: Optional[dict] = None,
    error: Optional[str] = None,
) -> dict:
    report = {
        "doc_id": item.get("doc_id"),
        "source_path": item.get("source_path") or item.get("path"),
        "action": action,
        "status": status,
        "stage": stage,
        "file_type": item.get("file_type"),
        "file_size": item.get("file_size"),
        "block_count": block_count,
        "chunk_count": chunk_count,
        "error": error,
    }
    if quality:
        report.update(quality)
    return report


def _finalize_and_save_ingestion_report(report: dict) -> None:
    report["finished_at"] = _utc_now_iso()
    documents = report["documents"]
    succeeded = sum(1 for item in documents if item.get("status") == "success")
    failed = sum(1 for item in documents if item.get("status") == "failed")
    total_block_count = sum(item.get("block_count") or 0 for item in documents)
    total_chunk_count = sum(item.get("chunk_count") or 0 for item in documents)

    failed_by_stage: Dict[str, int] = {}
    file_type_stats: Dict[str, dict] = {}

    for item in documents:
        status = item.get("status")
        stage = item.get("stage") or "unknown"
        file_type = item.get("file_type") or "unknown"
        block_count = item.get("block_count") or 0
        chunk_count = item.get("chunk_count") or 0

        if status == "failed":
            failed_by_stage[stage] = failed_by_stage.get(stage, 0) + 1

        stats = file_type_stats.setdefault(
            file_type,
            {
                "document_count": 0,
                "success_count": 0,
                "failed_count": 0,
                "block_count": 0,
                "chunk_count": 0,
            },
        )
        stats["document_count"] += 1
        if status == "success":
            stats["success_count"] += 1
        elif status == "failed":
            stats["failed_count"] += 1
        stats["block_count"] += block_count
        stats["chunk_count"] += chunk_count

    report["summary"]["succeeded"] = succeeded
    report["summary"]["failed"] = failed
    report["summary"]["total_block_count"] = total_block_count
    report["summary"]["total_chunk_count"] = total_chunk_count
    report["summary"]["avg_blocks_per_doc"] = round(total_block_count / succeeded, 2) if succeeded else 0
    report["summary"]["avg_chunks_per_doc"] = round(total_chunk_count / succeeded, 2) if succeeded else 0
    report["summary"]["failed_by_stage"] = failed_by_stage
    report["summary"]["file_type_stats"] = file_type_stats

    os.makedirs(app_settings.audit_dir, exist_ok=True)
    latest_path = os.path.join(app_settings.audit_dir, "ingestion_report_latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def scan_documents(docs_dir: str, file_extensions: List[str]) -> List[str]:
    document_paths = []
    for root, _, files in os.walk(docs_dir):
        for file in files:
            if any(file.endswith(ext) for ext in file_extensions):
                document_paths.append(os.path.join(root, file))
    return document_paths


def load_documents(document_paths: List[str], path_to_doc_id: Dict[str, str]) -> List[Document]:
    parser_extensions = (".pdf", ".docx", ".html", ".htm", ".txt", ".md", ".markdown")
    parser_paths = [p for p in document_paths if p.lower().endswith(parser_extensions)]
    other_paths = [p for p in document_paths if not p.lower().endswith(parser_extensions)]

    documents: List[Document] = []

    if other_paths:
        reader = SimpleDirectoryReader(input_files=other_paths, encoding="utf-8")
        documents.extend(reader.load_data())

    if not parser_paths:
        return documents

    if (
        parse_file_to_blocks is None
        or blocks_to_llama_documents is None
        or blocks_to_markdown is None
        or save_section_markdown_audit is None
        or save_markdown_audit is None
        or clean_blocks is None
        or clean_blocks_by_file_type is None
        or fix_titles is None
        or probe_pdf_text_layer is None
        or analyze_block_quality is None
    ):
        reader = SimpleDirectoryReader(input_files=parser_paths, encoding="utf-8")
        documents.extend(reader.load_data())
        return documents

    audit_dir = os.path.join(app_settings.audit_dir, "parsed_md")
    section_audit_dir = os.path.join(app_settings.audit_dir, "section_md")
    os.makedirs(audit_dir, exist_ok=True)
    os.makedirs(section_audit_dir, exist_ok=True)

    for parser_path in parser_paths:
        rel_path = _normalize_rel_path(parser_path, app_settings.docs_dir)
        doc_id = path_to_doc_id.get(rel_path) or os.path.splitext(os.path.basename(rel_path))[0]
        file_type = os.path.splitext(parser_path)[1].lower()
        quality_metadata: Dict[str, object] = {}

        if file_type == ".pdf":
            try:
                quality_metadata = probe_pdf_text_layer(parser_path)
            except Exception as e:
                quality_metadata = {
                    "pdf_probe_error": str(e),
                    "ocr_status": "unknown",
                }

        try:
            blocks = parse_file_to_blocks(
                parser_path,
                doc_id=doc_id,
                source_path=rel_path,
            )
        except Exception as e:
            raise IngestionStageError("parse", f"{rel_path}: {e}") from e

        try:
            blocks = clean_blocks_by_file_type(blocks, file_type)
            blocks = fix_titles(blocks)
            quality_metadata.update(
                analyze_block_quality(
                    blocks,
                    file_type=file_type,
                    pdf_probe=quality_metadata,
                )
            )
        except Exception as e:
            raise IngestionStageError("clean", f"{rel_path}: {e}") from e

        try:
            md = blocks_to_markdown(blocks)
            save_markdown_audit(md, out_dir=audit_dir, doc_id=doc_id, source_path=rel_path)
        except Exception as e:
            raise IngestionStageError("audit", f"{rel_path}: {e}") from e

        try:
            section_documents = blocks_to_llama_documents(blocks)
            if quality_metadata:
                for doc in section_documents:
                    doc.metadata.update(quality_metadata)
            save_section_markdown_audit(
                section_documents,
                out_dir=section_audit_dir,
                doc_id=doc_id,
                source_path=rel_path,
            )
            documents.extend(section_documents)
        except Exception as e:
            raise IngestionStageError("parse", f"{rel_path}: {e}") from e

    return documents


def _hierarchical_chunk_sizes() -> List[int]:
    raw = str(getattr(app_settings, "hierarchical_chunk_sizes", "2000,800,300"))
    sizes = [int(item.strip()) for item in raw.split(",") if item.strip()]
    if len(sizes) < 2:
        raise ValueError("hierarchical_chunk_sizes must contain at least two levels")
    return sizes


def merge_documents_for_hierarchy(documents: List[Document]) -> tuple[List[Document], Dict[str, dict]]:
    by_doc: Dict[str, List[Document]] = {}
    for doc in documents:
        doc_id = doc.metadata.get("doc_id") or doc.id_
        by_doc.setdefault(str(doc_id), []).append(doc)

    merged_documents: List[Document] = []
    storage_metadata_by_doc_id: Dict[str, dict] = {}
    for doc_id, doc_items in by_doc.items():
        doc_items = sorted(doc_items, key=lambda item: int(item.metadata.get("section_index") or 0))
        parts: List[str] = []
        first_metadata = dict(doc_items[0].metadata or {})
        section_count = 0
        page_ranges = []

        for item in doc_items:
            text = (item.text or "").strip()
            if not text:
                continue
            section_title = item.metadata.get("section_title")
            if section_title and not text.lstrip().startswith("#"):
                parts.append(f"## {section_title}\n\n{text}")
            else:
                parts.append(text)
            section_count += 1
            page_range = item.metadata.get("page_range")
            if page_range:
                page_ranges.append(str(page_range))

        metadata = {
            "doc_id": doc_id,
            "source_path": first_metadata.get("source_path") or "",
            "file_type": first_metadata.get("file_type") or "",
            "section_count": section_count,
            "page_range": ",".join(dict.fromkeys(page_ranges[:20])),
            "chunk_strategy": "hierarchical",
        }
        
        if "paragraph_id" in first_metadata:
            metadata["paragraph_id"] = first_metadata["paragraph_id"]
        if "source" in first_metadata:
            metadata["source"] = first_metadata["source"]
        if "title" in first_metadata:
            metadata["title"] = first_metadata["title"]
        storage_metadata_by_doc_id[doc_id] = metadata
        split_metadata = {"doc_id": doc_id}
        if "paragraph_id" in first_metadata:
            split_metadata["paragraph_id"] = first_metadata["paragraph_id"]
        merged_documents.append(Document(text="\n\n".join(parts), metadata=split_metadata, id_=doc_id))

    return merged_documents, storage_metadata_by_doc_id


def chunk_documents_hierarchical(documents: List[Document], chunk_overlap: int) -> List[TextNode]:
    merged_documents, storage_metadata_by_doc_id = merge_documents_for_hierarchy(documents)
    parser = HierarchicalNodeParser.from_defaults(
        chunk_sizes=_hierarchical_chunk_sizes(),
        chunk_overlap=chunk_overlap,
        include_metadata=True,
    )
    all_nodes = parser.get_nodes_from_documents(merged_documents)
    leaf_ids = {node.node_id for node in get_leaf_nodes(all_nodes)}
    root_ids = {node.node_id for node in get_root_nodes(all_nodes)}

    for node in all_nodes:
        doc_id = str(node.metadata.get("doc_id") or "")
        storage_metadata = dict(storage_metadata_by_doc_id.get(doc_id, {}))
        parent_id = ""
        parent = node.relationships.get(NodeRelationship.PARENT)
        if parent is not None:
            parent_id = str(parent.node_id)

        if node.node_id in leaf_ids:
            role = "leaf"
        elif node.node_id in root_ids:
            role = "root"
        else:
            role = "parent"

        node.metadata = build_hierarchy_node_metadata(
            base_metadata={
                **storage_metadata,
                **dict(node.metadata or {}),
                "doc_id": doc_id,
            },
            node_id=node.node_id,
            parent_id=parent_id,
            role=role,
            source_section_count=int(storage_metadata.get("section_count") or 0),
        )
        if doc_id:
            node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(node_id=doc_id)

    return all_nodes


def select_index_nodes(nodes: List[TextNode]) -> List[TextNode]:
    return [node for node in nodes if node.metadata.get("chunk_role") == "leaf"]


def get_milvus_vector_store(settings: app_settings, index_dir: str, collection_name: str, dim: int = 1536) -> MilvusVectorStore:
    os.makedirs(index_dir, exist_ok=True)
    uri = settings.milvus_uri

    vector_store = MilvusVectorStore(
        uri=uri,
        collection_name=collection_name,
        dim=dim,
        overwrite=False,
    )

    return vector_store


def get_or_create_milvus_index(
    settings: app_settings,
    index_dir: str,
    collection_name: str,
    dim: int = 1536
) -> VectorStoreIndex:
    vector_store = get_milvus_vector_store(
        settings=settings,
        index_dir=index_dir,
        collection_name=collection_name,
        dim=dim
    )

    if os.path.exists(os.path.join(index_dir, "docstore.json")) and not app_settings.parent_store_enabled:
        print("✅ 发现本地已存在的索引状态，正在加载...")
        storage_context = StorageContext.from_defaults(
            vector_store=vector_store,
            persist_dir=index_dir
        )
        return load_index_from_storage(storage_context)
    else:
        print("ℹ️ 未发现本地索引状态，正在创建全新索引环境...")
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        return VectorStoreIndex([], storage_context=storage_context)


def ensure_doc_ids_and_metadata(documents: List[Document], path_to_doc_id: Dict[str, str]) -> List[Document]:
    for doc in documents:
        existing_doc_id = doc.metadata.get("doc_id")
        existing_source_path = doc.metadata.get("source_path")

        if existing_doc_id and existing_source_path:
            doc.id_ = existing_doc_id
            continue

        file_path = doc.metadata.get("file_path", "")
        if file_path:
            rel_path = _normalize_rel_path(file_path, app_settings.docs_dir)
            fallback_doc_id = os.path.splitext(os.path.basename(rel_path))[0]
            doc_id = path_to_doc_id.get(rel_path) or fallback_doc_id

            doc.id_ = doc_id
            doc.metadata["doc_id"] = doc_id
            doc.metadata["source_path"] = rel_path
            continue

        if existing_source_path and not existing_doc_id:
            fallback_doc_id = os.path.splitext(os.path.basename(existing_source_path))[0]
            doc_id = path_to_doc_id.get(existing_source_path) or fallback_doc_id
            doc.id_ = doc_id
            doc.metadata["doc_id"] = doc_id
            continue

        doc.metadata["doc_id"] = doc.id_
        doc.metadata["source_path"] = existing_source_path or "unknown"

    return documents


def safe_delete_doc(index: VectorStoreIndex, doc_id: str):
    try:
        if index.docstore.get_ref_doc_info(doc_id) is not None:
            index.delete_ref_doc(doc_id, delete_from_docstore=True)
            print(f"    ✅ [Docstore 命中] 成功删除本地记录及 Milvus 对应节点: {doc_id}")
        else:
            print(f"    ⚠️ [Docstore 未命中] 本地未记录该文档，触发 Milvus 底层强制删除...")
            index.vector_store.delete(doc_id)
            print(f"    ✅ [强制清理完成] 已执行底层向量删除 {doc_id}")
    except Exception as e:
        print(f"    ❌ [清理异常] 删除 {doc_id} 时发生错误: {e}")


def apply_diff_to_milvus(
    index: VectorStoreIndex,
    diff_dict: Dict[str, List[dict]],
    chunk_size: int,
    chunk_overlap: int
):
    report = _new_ingestion_report(diff_dict)
    deleted_items = diff_dict.get("deleted", [])
    updated_items = diff_dict.get("updated", [])
    added_items = diff_dict.get("added", [])
    changed_items = added_items + updated_items + deleted_items

    parent_store = None
    if app_settings.parent_store_enabled:
        try:
            if build_parent_store is None:
                raise RuntimeError("PostgreSQL parent store is unavailable. Check sqlalchemy/psycopg dependencies.")
            parent_store = build_parent_store(app_settings.postgres_url)
        except Exception as e:
            for item in changed_items:
                if item in added_items:
                    action = "added"
                elif item in updated_items:
                    action = "updated"
                else:
                    action = "deleted"
                report["documents"].append(_report_item(item, action, "failed", stage="parent_store", error=str(e)))
            _finalize_and_save_ingestion_report(report)
            raise

    print(f"\n🚀 开始执行增量同步 新增 {len(added_items)}, 更新 {len(updated_items)}, 删除 {len(deleted_items)}")

    for item in deleted_items:
        doc_id = item["doc_id"]
        report["documents"].append(_report_item(item, "deleted", "success", stage="completed", block_count=0, chunk_count=0))
        print(f"🗑️ [Delete] 正在处理删除文档: {doc_id}")
        if parent_store is not None:
            parent_store.delete_by_doc_ids([doc_id])
        safe_delete_doc(index, doc_id)

    for item in updated_items:
        doc_id = item["doc_id"]
        print(f"🔄 [Update-Delete] 正在清理待更新的旧版本文档 {doc_id}")
        if parent_store is not None:
            parent_store.delete_by_doc_ids([doc_id])
        safe_delete_doc(index, doc_id)

    files_to_index: List[str] = []
    path_to_doc_id: Dict[str, str] = {}
    path_to_file_type: Dict[str, str] = {}

    for item in added_items + updated_items:
        rel_path = _normalize_rel_path(item["path"], app_settings.docs_dir)
        abs_path = os.path.join(app_settings.docs_dir, rel_path)

        files_to_index.append(abs_path)
        path_to_doc_id[rel_path] = item["doc_id"]
        path_to_file_type[rel_path] = item.get("file_type") or "unknown"

    if files_to_index:
        print(f"\n📂 [Upsert] 正在加载 {len(files_to_index)} 个文件并准备分片...")
        try:
            loaded_docs = load_documents(files_to_index, path_to_doc_id)
            loaded_docs = ensure_doc_ids_and_metadata(loaded_docs, path_to_doc_id)
        except IngestionStageError as e:
            for item in added_items:
                report["documents"].append(_report_item(item, "added", "failed", stage=e.stage, error=str(e)))
            for item in updated_items:
                report["documents"].append(_report_item(item, "updated", "failed", stage=e.stage, error=str(e)))
            _finalize_and_save_ingestion_report(report)
            raise
        except Exception as e:
            for item in added_items:
                report["documents"].append(_report_item(item, "added", "failed", stage="parse", error=str(e)))
            for item in updated_items:
                report["documents"].append(_report_item(item, "updated", "failed", stage="parse", error=str(e)))
            _finalize_and_save_ingestion_report(report)
            raise

        try:
            chunked_nodes = chunk_documents_hierarchical(loaded_docs, chunk_overlap)
            index_nodes = select_index_nodes(chunked_nodes)
            report["section_stats"] = _build_section_stats(loaded_docs)
            report["hierarchy_stats"] = _build_hierarchy_stats(chunked_nodes)
            report["chunk_stats"] = _build_chunk_stats(index_nodes, path_to_file_type)
        except Exception as e:
            for item in added_items:
                report["documents"].append(_report_item(item, "added", "failed", stage="chunk", error=str(e)))
            for item in updated_items:
                report["documents"].append(_report_item(item, "updated", "failed", stage="chunk", error=str(e)))
            _finalize_and_save_ingestion_report(report)
            raise

        print("\n--- 分片后的 TextNode 详情 ---")
        doc_chunk_counters: Dict[str, int] = {}
        doc_block_counts: Dict[str, int] = {}
        doc_quality = _build_doc_quality_map(loaded_docs)

        for doc in loaded_docs:
            doc_id = doc.metadata.get("doc_id", "unknown_doc")
            doc_block_counts[doc_id] = doc_block_counts.get(doc_id, 0) + (doc.metadata.get("block_count") or 1)

        for i, node in enumerate(chunked_nodes):
            doc_id = node.metadata.get("doc_id", "unknown_doc")

            idx = doc_chunk_counters.get(doc_id, 0)
            doc_chunk_counters[doc_id] = idx + 1

            stable_chunk_id = node.metadata.get("chunk_id") or node.node_id

            node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(node_id=doc_id)

            node.metadata["chunk_id"] = stable_chunk_id
            node.metadata["chunk_index"] = idx

            print(f"Node {i+1} (ID: {node.id_}, 来自: {node.metadata.get('source_path')}):")
            print("--------------------------------------------------")
            print(f"内容:\n{node.text[:200]}..." if len(node.text) > 200 else f"内容:\n{node.text}")
            print(f"元数据:\n{node.metadata}")
            print("--------------------------------------------------\n")

        print(f"✅ [Upsert] 正在将 {len(chunked_nodes)} 个新节点注册到 Docstore 并注入 Milvus...")

        index_nodes = select_index_nodes(chunked_nodes)
        doc_chunk_counters = {}
        for node in index_nodes:
            doc_id = node.metadata.get("doc_id", "unknown_doc")
            doc_chunk_counters[doc_id] = doc_chunk_counters.get(doc_id, 0) + 1

        if parent_store is not None:
            try:
                parent_store.upsert_nodes(chunked_nodes)
            except Exception as e:
                for item in added_items:
                    report["documents"].append(_report_item(item, "added", "failed", stage="write_chunk_store", error=str(e)))
                for item in updated_items:
                    report["documents"].append(_report_item(item, "updated", "failed", stage="write_chunk_store", error=str(e)))
                _finalize_and_save_ingestion_report(report)
                raise

        try:
            index.docstore.add_documents(index_nodes, allow_update=True)
        except Exception as e:
            for item in added_items:
                report["documents"].append(_report_item(item, "added", "failed", stage="write_docstore", error=str(e)))
            for item in updated_items:
                report["documents"].append(_report_item(item, "updated", "failed", stage="write_docstore", error=str(e)))
            _finalize_and_save_ingestion_report(report)
            raise

        try:
            index.insert_nodes(index_nodes)
        except Exception as e:
            for item in added_items:
                report["documents"].append(_report_item(item, "added", "failed", stage="insert_milvus", error=str(e)))
            for item in updated_items:
                report["documents"].append(_report_item(item, "updated", "failed", stage="insert_milvus", error=str(e)))
            _finalize_and_save_ingestion_report(report)
            raise

        for item in added_items:
            doc_id = item.get("doc_id")
            report["documents"].append(
                _report_item(
                    item,
                    "added",
                    "success",
                    stage="completed",
                    block_count=doc_block_counts.get(doc_id, 0),
                    chunk_count=doc_chunk_counters.get(doc_id, 0),
                    quality=doc_quality.get(doc_id),
                )
            )
        for item in updated_items:
            doc_id = item.get("doc_id")
            report["documents"].append(
                _report_item(
                    item,
                    "updated",
                    "success",
                    stage="completed",
                    block_count=doc_block_counts.get(doc_id, 0),
                    chunk_count=doc_chunk_counters.get(doc_id, 0),
                    quality=doc_quality.get(doc_id),
                )
            )

    if not app_settings.parent_store_enabled:
        try:
            index.storage_context.persist(persist_dir=app_settings.index_dir)
        except Exception as e:
            for item in added_items:
                report["documents"].append(_report_item(item, "added", "failed", stage="persist_docstore", error=str(e)))
            for item in updated_items:
                report["documents"].append(_report_item(item, "updated", "failed", stage="persist_docstore", error=str(e)))
            _finalize_and_save_ingestion_report(report)
            raise

    _finalize_and_save_ingestion_report(report)
    print("\nSync completed. PostgreSQL chunk store and Milvus are updated.")


if __name__ == "__main__":
    print(f"--- Starting Incremental Milvus Indexing Pipeline ---")

    Settings.llm = OpenAILike(
        model=app_settings.llm_model,
        api_base=app_settings.llm_api_base,
        api_key=app_settings.llm_api_key,
        is_chat_model=True,
    )
    Settings.embed_model = OpenAIEmbedding(
        model="text-embedding-ada-002",
        model_name=app_settings.embedding_model,
        api_base=app_settings.embedding_api_base,
        api_key=app_settings.qwen_llm_api_key,
        embed_batch_size=app_settings.embedding_batch_size,
    )

    state_path = os.path.join(app_settings.index_dir, "ingest_state.json")
    state = load_state(state_path)
    diff_dict, next_state = diff_docs(app_settings.docs_dir, state)

    if not any([diff_dict["added"], diff_dict["updated"], diff_dict["deleted"]]):
        print("No document changes detected. Skip indexing.")
        exit(0)

    dim = 1536
    index = get_or_create_milvus_index(
        settings=app_settings,
        index_dir=app_settings.index_dir,
        collection_name=app_settings.milvus_collection,
        dim=dim
    )

    apply_diff_to_milvus(
        index=index,
        diff_dict=diff_dict,
        chunk_size=app_settings.chunk_size,
        chunk_overlap=app_settings.chunk_overlap
    )

    save_state(state_path, next_state)
    print("--- Milvus Indexing Pipeline Completed ---")
