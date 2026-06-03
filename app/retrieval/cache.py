"""Redis-backed retrieval cache.

The cache stores final retrieval nodes after hybrid retrieval, parent context /
auto-merging, and optional rerank. Keys include the knowledge-base version and
retrieval settings so document updates naturally invalidate old results.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import redis
from llama_index.core.schema import NodeWithScore, TextNode

from app.config import settings as app_settings


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_fingerprint(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        stat = path.stat()
        return f"{int(stat.st_mtime_ns)}:{stat.st_size}"
    except Exception:
        return ""


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip().lower())


def knowledge_base_version() -> str:
    """Return a stable version hash for cache invalidation."""

    ingest_hash = _file_fingerprint(Path(app_settings.index_dir) / "ingest_state.json")
    bm25_hash = _file_fingerprint(Path(app_settings.bm25_state_path))
    raw = json.dumps(
        {
            "ingest": ingest_hash,
            "bm25": bm25_hash,
            "vector_store_backend": app_settings.vector_store_backend,
            "pgvector_table": app_settings.pgvector_table_name,
            "milvus_collection": app_settings.milvus_collection,
        },
        sort_keys=True,
    )
    return _sha256_text(raw)[:16]


def retrieval_cache_key(query: str, *, top_k: int) -> str:
    config_payload = {
        "query": _normalize_query(query),
        "top_k": top_k,
        "kb_version": knowledge_base_version(),
        "rerank_enabled": app_settings.rerank_enabled,
        "rerank_min_candidates": app_settings.rerank_min_candidates,
        "auto_merging_enabled": app_settings.auto_merging_enabled,
        "auto_merging_threshold": app_settings.auto_merging_threshold,
        "bm25_k1": app_settings.bm25_k1,
        "bm25_b": app_settings.bm25_b,
    }
    digest = _sha256_text(json.dumps(config_payload, ensure_ascii=False, sort_keys=True))
    return f"rag:retrieval:v1:{digest}"


def _node_to_payload(node_with_score: NodeWithScore) -> dict[str, Any]:
    node = node_with_score.node
    return {
        "node_id": str(node.node_id),
        "text": node.get_content(),
        "metadata": dict(node.metadata or {}),
        "score": node_with_score.score,
    }


def _payload_to_node(payload: dict[str, Any]) -> NodeWithScore:
    node = TextNode(
        id_=str(payload.get("node_id") or ""),
        text=str(payload.get("text") or ""),
        metadata=dict(payload.get("metadata") or {}),
    )
    score = payload.get("score")
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    return NodeWithScore(node=node, score=score)


class RetrievalCache:
    def __init__(self, redis_client: redis.Redis | None):
        self.redis = redis_client

    @property
    def enabled(self) -> bool:
        return bool(app_settings.retrieval_cache_enabled and self.redis is not None)

    def get(self, query: str, *, top_k: int) -> tuple[list[NodeWithScore] | None, dict[str, Any]]:
        metadata = {
            "enabled": self.enabled,
            "hit": False,
            "key": retrieval_cache_key(query, top_k=top_k),
            "kb_version": knowledge_base_version(),
        }
        if not self.enabled:
            return None, metadata
        start = time.perf_counter()
        try:
            raw = self.redis.get(metadata["key"])
        except Exception as exc:
            metadata.update({"error": str(exc), "read_ms": round((time.perf_counter() - start) * 1000, 2)})
            return None, metadata
        metadata["read_ms"] = round((time.perf_counter() - start) * 1000, 2)
        if not raw:
            return None, metadata
        try:
            payload = json.loads(raw)
            nodes = [_payload_to_node(item) for item in payload.get("nodes") or []]
        except Exception as exc:
            metadata["error"] = str(exc)
            return None, metadata
        metadata.update(
            {
                "hit": True,
                "node_count": len(nodes),
                "created_at": payload.get("created_at"),
            }
        )
        return nodes, metadata

    def set(self, query: str, *, top_k: int, nodes: list[NodeWithScore]) -> dict[str, Any]:
        metadata = {
            "enabled": self.enabled,
            "hit": False,
            "key": retrieval_cache_key(query, top_k=top_k),
            "kb_version": knowledge_base_version(),
            "node_count": len(nodes),
        }
        if not self.enabled:
            return metadata
        payload = {
            "created_at": int(time.time()),
            "kb_version": metadata["kb_version"],
            "nodes": [_node_to_payload(node) for node in nodes],
        }
        start = time.perf_counter()
        try:
            self.redis.setex(
                metadata["key"],
                app_settings.retrieval_cache_ttl,
                json.dumps(payload, ensure_ascii=False),
            )
            metadata["stored"] = True
        except Exception as exc:
            metadata.update({"stored": False, "error": str(exc)})
        metadata["write_ms"] = round((time.perf_counter() - start) * 1000, 2)
        return metadata
