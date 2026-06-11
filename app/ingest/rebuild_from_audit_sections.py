"""Rebuild the Chinese finance index from parsed section audit files.

Use this when PDF parsing has already completed and only chunking settings need
to change. It reads ``audit_dir/section_md`` and rebuilds PostgreSQL parent
store, Chroma vector collection, BM25 state, and ingest state without parsing
the original PDFs again.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.schema import Document, NodeRelationship, RelatedNodeInfo, TextNode
from llama_index.core.settings import Settings
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai_like import OpenAILike

from app.config import settings as app_settings
from app.ingest.milvus_loader import (
    _build_chunk_stats,
    _build_hierarchy_stats,
    _build_section_stats,
    _finalize_and_save_ingestion_report,
    _new_ingestion_report,
    _print_progress,
    _report_item,
    chunk_documents_hierarchical,
    get_default_vector_store,
    select_index_nodes,
)
from app.ingest.sync import diff_docs, load_state, save_state
from app.retrieval.bm25_state import rebuild_bm25_state
from app.storage.parent_store import build_parent_store


SECTION_PATTERN = re.compile(
    r"(?:^|\n)## Section\s+(\d+)\s*\n\n```text\n(.*?)\n```\s*\n\n(.*?)(?=\n---\s*\n\n## Section\s+\d+|\Z)",
    flags=re.DOTALL,
)


def parse_simple_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    raw = text[3:end].strip()
    body = text[end + len("\n---") :].lstrip()
    metadata: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    return metadata, body


def parse_metadata_block(raw: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in {"block_count", "length"}:
            try:
                metadata[key] = int(value)
            except ValueError:
                metadata[key] = value
            continue
        metadata[key] = value
    return metadata


def load_section_documents(section_dir: Path) -> list[Document]:
    documents: list[Document] = []
    files = sorted(section_dir.glob("*.md"))
    if not files:
        raise RuntimeError(f"No section markdown files found in {section_dir}")

    for file_index, path in enumerate(files, start=1):
        text = path.read_text(encoding="utf-8")
        frontmatter, body = parse_simple_frontmatter(text)
        doc_id = frontmatter.get("doc_id") or path.stem
        source_path = frontmatter.get("source_path") or ""
        matches = list(SECTION_PATTERN.finditer(body))

        for match in matches:
            section_index = int(match.group(1))
            metadata = parse_metadata_block(match.group(2))
            section_text = match.group(3).strip()
            if not section_text:
                continue
            metadata.update(
                {
                    "doc_id": doc_id,
                    "source_path": metadata.get("source_path") or source_path,
                    "file_type": Path(source_path).suffix.lower() or ".pdf",
                    "section_index": section_index,
                    "section_id": metadata.get("section_id")
                    or f"{doc_id}::section::{section_index}",
                }
            )
            documents.append(
                Document(
                    text=section_text,
                    metadata=metadata,
                    id_=str(metadata["section_id"]),
                )
            )
        _print_progress("[LoadSections]", file_index, len(files), path.name)

    return documents


def setup_llamaindex() -> None:
    Settings.llm = OpenAILike(
        model=app_settings.llm_model,
        api_base=app_settings.llm_api_base,
        api_key=app_settings.llm_api_key,
        is_chat_model=True,
    )
    Settings.embed_model = OpenAIEmbedding(
        model_name=app_settings.embedding_model,
        api_base=app_settings.embedding_api_base,
        api_key=app_settings.qwen_llm_api_key,
        embed_batch_size=app_settings.embedding_batch_size,
    )


def reset_chroma_collection() -> None:
    if (app_settings.vector_store_backend or "chroma").strip().lower() != "chroma":
        raise RuntimeError("This rebuild script currently resets only Chroma collections.")
    import chromadb

    Path(app_settings.chroma_dir).mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=app_settings.chroma_dir)
    collection_name = app_settings.chroma_collection
    try:
        client.delete_collection(collection_name)
        print(f"[Reset] Chroma collection deleted: {collection_name}")
    except Exception as exc:
        message = str(exc).lower()
        if "does not exist" not in message and "not found" not in message:
            raise
        print(f"[Reset] Chroma collection not found, will create a fresh one: {collection_name}")


def prepare_report(documents: list[Document]) -> tuple[dict, list[dict]]:
    seen: dict[str, dict] = {}
    for doc in documents:
        doc_id = str(doc.metadata.get("doc_id") or "")
        if not doc_id or doc_id in seen:
            continue
        source_path = str(doc.metadata.get("source_path") or "")
        seen[doc_id] = {
            "path": source_path,
            "doc_id": doc_id,
            "source_path": source_path,
            "file_name": os.path.basename(source_path),
            "file_type": Path(source_path).suffix.lower() or ".pdf",
            "file_size": None,
        }
    items = list(seen.values())
    return _new_ingestion_report({"added": items, "updated": [], "deleted": []}), items


def update_original_ingest_state() -> None:
    state_path = os.path.join(app_settings.index_dir, "ingest_state.json")
    state = load_state(state_path)
    _, next_state = diff_docs(app_settings.docs_dir, state)
    save_state(state_path, next_state)
    print(f"[State] ingest_state saved: {state_path}")


def rebuild_from_sections(section_dir: Path, *, reset_vector: bool = True) -> dict:
    setup_llamaindex()
    documents = load_section_documents(section_dir)
    if not documents:
        raise RuntimeError("No section documents were loaded.")

    doc_ids = sorted({str(doc.metadata.get("doc_id") or "") for doc in documents if doc.metadata.get("doc_id")})
    path_to_file_type = {
        str(doc.metadata.get("source_path") or ""): str(doc.metadata.get("file_type") or ".pdf")
        for doc in documents
        if doc.metadata.get("source_path")
    }
    report, report_source_items = prepare_report(documents)
    report["rebuild_from_audit_sections"] = {
        "section_dir": str(section_dir),
        "hierarchical_chunk_sizes": app_settings.hierarchical_chunk_sizes,
        "chunk_overlap": app_settings.chunk_overlap,
        "embedding_model": app_settings.embedding_model,
        "embedding_batch_size": app_settings.embedding_batch_size,
        "vector_store_backend": app_settings.vector_store_backend,
        "chroma_collection": app_settings.chroma_collection,
    }

    print("\n[Chunk] 正在基于 section_md 重新生成层级 chunk...")
    chunked_nodes = chunk_documents_hierarchical(documents, app_settings.chunk_overlap)
    doc_chunk_counters: dict[str, int] = {}
    for node in chunked_nodes:
        doc_id = str(node.metadata.get("doc_id") or "unknown_doc")
        idx = doc_chunk_counters.get(doc_id, 0)
        doc_chunk_counters[doc_id] = idx + 1
        stable_chunk_id = node.metadata.get("chunk_id") or node.node_id
        node.relationships[NodeRelationship.SOURCE] = RelatedNodeInfo(node_id=doc_id)
        node.metadata["chunk_id"] = stable_chunk_id
        node.metadata["chunk_index"] = idx

    index_nodes = select_index_nodes(chunked_nodes)
    report["section_stats"] = _build_section_stats(documents)
    report["hierarchy_stats"] = _build_hierarchy_stats(chunked_nodes)
    report["chunk_stats"] = _build_chunk_stats(index_nodes, path_to_file_type)
    print(f"[Chunk] 完成：sections={len(documents)}, hierarchy_nodes={len(chunked_nodes)}, leaf_nodes={len(index_nodes)}")

    parent_store = build_parent_store(app_settings.postgres_url) if app_settings.parent_store_enabled else None
    if parent_store is not None:
        print(f"[Reset] PostgreSQL parent store 清理文档数：{len(doc_ids)}")
        parent_store.delete_by_doc_ids(doc_ids)

    if reset_vector:
        reset_chroma_collection()

    vector_store = get_default_vector_store(
        settings=app_settings,
        index_dir=app_settings.index_dir,
        collection_name=app_settings.milvus_collection,
        dim=app_settings.pgvector_embed_dim,
    )
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex([], storage_context=storage_context)

    if parent_store is not None:
        print(f"[Store] 正在写入 PostgreSQL parent store：{len(chunked_nodes)} nodes")
        parent_store.upsert_nodes(chunked_nodes)
        print("[Store] PostgreSQL parent store 写入完成")

    print(f"[Docstore] 正在注册 leaf nodes：{len(index_nodes)}")
    index.docstore.add_documents(index_nodes, allow_update=True)
    print("[Docstore] 注册完成")

    print(f"[Vector] 正在写入向量库：{len(index_nodes)} leaf nodes")
    index.insert_nodes(index_nodes)
    print("[Vector] 向量库写入完成")

    print(f"[BM25State] 正在重建 BM25 状态：{len(index_nodes)} leaf nodes")
    rebuild_bm25_state(index_nodes)
    print("[BM25State] 重建完成")

    sections_by_doc: dict[str, int] = {}
    leaf_by_doc: dict[str, int] = {}
    for doc in documents:
        doc_id = str(doc.metadata.get("doc_id") or "unknown_doc")
        sections_by_doc[doc_id] = sections_by_doc.get(doc_id, 0) + 1
    for node in index_nodes:
        doc_id = str(node.metadata.get("doc_id") or "unknown_doc")
        leaf_by_doc[doc_id] = leaf_by_doc.get(doc_id, 0) + 1

    report["documents"] = []
    for item in report_source_items:
        doc_id = str(item.get("doc_id") or "")
        report["documents"].append(
            _report_item(
            item,
            "rebuilt_from_sections",
            "success",
            stage="completed",
            block_count=sections_by_doc.get(doc_id, 0),
            chunk_count=leaf_by_doc.get(doc_id, 0),
            )
        )

    _finalize_and_save_ingestion_report(report)
    update_original_ingest_state()
    print("\nRebuild completed from audit section markdown.")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild index from audit section markdown files.")
    parser.add_argument("--section-dir", type=Path, default=Path(app_settings.audit_dir) / "section_md")
    parser.add_argument("--hierarchical-chunk-sizes", default=None)
    parser.add_argument("--chunk-overlap", type=int, default=None)
    parser.add_argument("--yes", action="store_true", help="Confirm index rebuild and collection reset.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.hierarchical_chunk_sizes:
        app_settings.hierarchical_chunk_sizes = args.hierarchical_chunk_sizes
    if args.chunk_overlap is not None:
        app_settings.chunk_overlap = args.chunk_overlap

    print("--- Rebuild Index From Audit Sections ---")
    print(f"section_dir: {args.section_dir}")
    print(f"docs_dir: {app_settings.docs_dir}")
    print(f"index_dir: {app_settings.index_dir}")
    print(f"audit_dir: {app_settings.audit_dir}")
    print(f"hierarchical_chunk_sizes: {app_settings.hierarchical_chunk_sizes}")
    print(f"chunk_overlap: {app_settings.chunk_overlap}")
    print(f"embedding_model: {app_settings.embedding_model}")
    print(f"vector_store_backend: {app_settings.vector_store_backend}")
    print(f"chroma_collection: {app_settings.chroma_collection}")

    if not args.yes:
        print("\nDry run only. Add --yes to rebuild and reset the current Chroma collection.")
        return

    rebuild_from_sections(args.section_dir)


if __name__ == "__main__":
    main()
