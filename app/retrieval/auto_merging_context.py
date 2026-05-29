from __future__ import annotations

from typing import Any, Dict, List, Optional

from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from pydantic import PrivateAttr

from app.metadata_schema import CanonicalChunkMetadata, build_auto_merged_metadata


class AutoMergingContextPostprocessor(BaseNodePostprocessor):
    """Merge retrieved leaf chunks into parent context when enough siblings hit."""

    _parent_store: Any = PrivateAttr()
    _merge_threshold: float = PrivateAttr()
    _max_merge_chars: Optional[int] = PrivateAttr(default=None)

    def __init__(
        self,
        parent_store: Any,
        merge_threshold: float = 0.5,
        max_merge_chars: Optional[int] = None,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self._parent_store = parent_store
        self._merge_threshold = merge_threshold
        self._max_merge_chars = max_merge_chars

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        parent_groups = self._group_leaf_nodes_by_parent(nodes)
        merge_parent_ids = self._select_merge_parent_ids(parent_groups)
        if not merge_parent_ids:
            return nodes

        output_nodes: List[NodeWithScore] = []
        emitted_parent_ids = set()

        for node_with_score in nodes:
            metadata = dict(node_with_score.node.metadata or {})
            parent_id = CanonicalChunkMetadata.from_raw(metadata).parent_id

            if parent_id not in merge_parent_ids:
                output_nodes.append(node_with_score)
                continue

            if parent_id in emitted_parent_ids:
                continue

            parent_node = self._build_parent_node(
                parent_id=parent_id,
                children=parent_groups[parent_id],
            )
            if parent_node is None:
                output_nodes.extend(parent_groups[parent_id])
                emitted_parent_ids.add(parent_id)
                continue

            emitted_parent_ids.add(parent_id)
            output_nodes.append(parent_node)

        return output_nodes

    @staticmethod
    def _group_leaf_nodes_by_parent(nodes: List[NodeWithScore]) -> Dict[str, List[NodeWithScore]]:
        groups: Dict[str, List[NodeWithScore]] = {}
        seen_child_ids_by_parent: Dict[str, set[str]] = {}

        for node_with_score in nodes:
            node = node_with_score.node
            metadata = dict(node.metadata or {})
            canonical = CanonicalChunkMetadata.from_raw(metadata)
            if canonical.chunk_role not in ("unknown", "leaf"):
                continue

            parent_id = canonical.parent_id
            child_id = canonical.chunk_id or str(node.node_id or "")
            if not parent_id or not child_id:
                continue

            seen_child_ids_by_parent.setdefault(parent_id, set())
            if child_id in seen_child_ids_by_parent[parent_id]:
                continue

            seen_child_ids_by_parent[parent_id].add(child_id)
            groups.setdefault(parent_id, []).append(node_with_score)

        return groups

    def _select_merge_parent_ids(self, parent_groups: Dict[str, List[NodeWithScore]]) -> set[str]:
        merge_parent_ids = set()

        for parent_id, children in parent_groups.items():
            total_children = self._parent_store.count_children(parent_id, leaf_only=True)
            if total_children <= 0:
                continue

            merge_ratio = len(children) / total_children
            if merge_ratio >= self._merge_threshold:
                merge_parent_ids.add(parent_id)

        return merge_parent_ids

    def _build_parent_node(
        self,
        parent_id: str,
        children: List[NodeWithScore],
    ) -> Optional[NodeWithScore]:
        parent = self._parent_store.get_node(parent_id)
        if parent is None:
            return None

        parent_text = str(parent.get("text") or "").strip()
        if not parent_text:
            return None

        if self._max_merge_chars and len(parent_text) > self._max_merge_chars:
            parent_text = parent_text[: self._max_merge_chars].rstrip()

        child_scores = [child.score for child in children if child.score is not None]
        score = max(child_scores) if child_scores else children[0].score
        total_children = self._parent_store.count_children(parent_id, leaf_only=True)
        parent_metadata = build_auto_merged_metadata(
            parent_row=parent,
            child_metadatas=[dict(child.node.metadata or {}) for child in children],
            merge_threshold=self._merge_threshold,
            total_child_count=total_children,
        )

        return NodeWithScore(
            node=TextNode(
                id_=parent_id,
                text=parent_text,
                metadata=parent_metadata,
            ),
            score=score,
        )
