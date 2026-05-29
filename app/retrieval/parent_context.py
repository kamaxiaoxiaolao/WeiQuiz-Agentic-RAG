from __future__ import annotations

from typing import Any, Dict, List, Optional

from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from pydantic import PrivateAttr

from app.metadata_schema import CanonicalChunkMetadata, build_parent_context_metadata


class ParentContextPostprocessor(BaseNodePostprocessor):
    """Expand retrieved child chunks to their parent section context."""

    _parent_store: Any = PrivateAttr()
    _max_parent_chars: Optional[int] = PrivateAttr(default=None)

    def __init__(self, parent_store: Any, max_parent_chars: Optional[int] = None, **kwargs: Any):
        super().__init__(**kwargs)
        self._parent_store = parent_store
        self._max_parent_chars = max_parent_chars

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        expanded_nodes: List[NodeWithScore] = []
        seen_parent_ids = set()

        for node_with_score in nodes:
            child_node = node_with_score.node
            child_metadata = dict(child_node.metadata or {})
            parent_id = CanonicalChunkMetadata.from_raw(child_metadata).parent_id

            if not parent_id:
                child_metadata["parent_lookup_status"] = "missing_parent_id"
                child_node.metadata.update(child_metadata)
                expanded_nodes.append(node_with_score)
                continue

            parent = self._parent_store.get_parent(str(parent_id))
            if parent is None:
                child_metadata["parent_lookup_status"] = "not_found"
                child_node.metadata.update(child_metadata)
                expanded_nodes.append(node_with_score)
                continue

            if parent_id in seen_parent_ids:
                continue
            seen_parent_ids.add(parent_id)

            parent_text = str(parent.get("text") or "").strip()
            if self._max_parent_chars and len(parent_text) > self._max_parent_chars:
                parent_text = parent_text[: self._max_parent_chars].rstrip()

            parent_metadata = self._build_parent_metadata(
                child_metadata=child_metadata,
                parent=parent,
                child_text=child_node.text or "",
            )
            parent_node = TextNode(
                id_=str(parent_id),
                text=parent_text,
                metadata=parent_metadata,
            )
            expanded_nodes.append(NodeWithScore(node=parent_node, score=node_with_score.score))

        return expanded_nodes

    @staticmethod
    def _build_parent_metadata(
        *,
        child_metadata: Dict[str, Any],
        parent: Dict[str, Any],
        child_text: str,
    ) -> Dict[str, Any]:
        return build_parent_context_metadata(
            child_metadata=child_metadata,
            parent_row=parent,
            child_text=child_text,
        )
