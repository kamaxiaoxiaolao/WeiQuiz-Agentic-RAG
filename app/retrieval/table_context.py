from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from pydantic import PrivateAttr

from app.metadata_schema import CanonicalChunkMetadata

TABLE_ID_PATTERN = re.compile(r"\[TABLE_ID:\s*([^\]\n]+)\]", flags=re.IGNORECASE)
TABLE_PAGE_RANGE_PATTERN = re.compile(r"\[TABLE_PAGE_RANGE:\s*([^\]\n]+)\]", flags=re.IGNORECASE)


def extract_table_id(text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    metadata = metadata or {}
    raw_table_id = str(metadata.get("table_id") or "").strip()
    if raw_table_id:
        return raw_table_id
    match = TABLE_ID_PATTERN.search(text or "")
    return match.group(1).strip() if match else ""


def extract_table_page_range(text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
    metadata = metadata or {}
    raw_page_range = str(metadata.get("table_page_range") or metadata.get("page_range") or "").strip()
    if raw_page_range:
        return raw_page_range
    match = TABLE_PAGE_RANGE_PATTERN.search(text or "")
    return match.group(1).strip() if match else ""


class TableContextPostprocessor(BaseNodePostprocessor):
    """Expand table hits to all stored chunks that carry the same table_id."""

    _parent_store: Any = PrivateAttr()
    _max_table_chars: Optional[int] = PrivateAttr(default=None)

    def __init__(self, parent_store: Any, max_table_chars: Optional[int] = None, **kwargs: Any):
        super().__init__(**kwargs)
        self._parent_store = parent_store
        self._max_table_chars = max_table_chars

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        output_nodes: List[NodeWithScore] = []
        emitted_table_ids = set()

        for node_with_score in nodes:
            node = node_with_score.node
            metadata = dict(node.metadata or {})
            table_id = extract_table_id(node.text or "", metadata)
            if not table_id:
                output_nodes.append(node_with_score)
                continue
            if table_id in emitted_table_ids:
                continue
            emitted_table_ids.add(table_id)

            table_node = self._build_table_node(
                table_id=table_id,
                seed=node_with_score,
            )
            output_nodes.append(table_node or node_with_score)

        return output_nodes

    def _build_table_node(self, *, table_id: str, seed: NodeWithScore) -> Optional[NodeWithScore]:
        seed_metadata = dict(seed.node.metadata or {})
        seed_canonical = CanonicalChunkMetadata.from_raw(seed_metadata)
        doc_id = seed_canonical.doc_id
        if not doc_id:
            return None

        try:
            candidate_nodes = self._parent_store.list_chunk_nodes([doc_id], leaf_only=False)
        except TypeError:
            candidate_nodes = self._parent_store.list_chunk_nodes(doc_ids=[doc_id], leaf_only=False)

        matching_nodes = [
            node
            for node in candidate_nodes
            if extract_table_id(node.text or "", dict(node.metadata or {})) == table_id
            and dict(node.metadata or {}).get("chunk_role") != "root"
        ]
        if not matching_nodes:
            return None

        matching_nodes.sort(key=lambda item: int((item.metadata or {}).get("chunk_index") or 0))
        parts: List[str] = []
        seen_text = set()
        for node in matching_nodes:
            text = (node.text or "").strip()
            if not text or text in seen_text:
                continue
            seen_text.add(text)
            parts.append(text)

        table_text = "\n\n".join(parts).strip()
        if not table_text:
            return None
        if self._max_table_chars and len(table_text) > self._max_table_chars:
            table_text = table_text[: self._max_table_chars].rstrip()

        first_metadata = dict(matching_nodes[0].metadata or {})
        page_range = extract_table_page_range(table_text, first_metadata)
        child_metadata = dict(seed.node.metadata or {})
        child_canonical = CanonicalChunkMetadata.from_raw(child_metadata)
        metadata = {
            **first_metadata,
            "retrieval_mode": "table_context",
            "table_context_status": "found",
            "table_id": table_id,
            "table_page_range": page_range,
            "page_range": page_range or first_metadata.get("page_range") or child_canonical.page_range,
            "doc_id": doc_id,
            "source_path": seed_canonical.source_path or first_metadata.get("source_path") or "",
            "file_name": seed_canonical.file_name,
            "chunk_id": f"{table_id}::context",
            "child_chunk_id": child_canonical.chunk_id,
            "table_context_node_count": len(matching_nodes),
        }

        return NodeWithScore(
            node=TextNode(
                id_=f"{table_id}::context",
                text=table_text,
                metadata=metadata,
            ),
            score=seed.score,
        )
