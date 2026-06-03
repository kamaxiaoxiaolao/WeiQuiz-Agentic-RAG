"""Stateful BM25 sparse retrieval with incremental persistence.

The retriever keeps BM25 statistics in a JSON state file:

- vocab: stable token -> sparse dimension id
- doc_freq: token -> document frequency
- total_docs / sum_token_len: corpus statistics
- documents: chunk_id -> term frequencies and token length

This is more explicit than persisting a black-box BM25 index and lets ingestion
incrementally add/remove document chunks while keeping sparse statistics aligned
with the PostgreSQL chunk store and Milvus leaf nodes.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from llama_index.core.retrievers import BaseRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode

from app.config import settings as app_settings
from app.metadata_schema import CanonicalChunkMetadata


STATE_VERSION = 1
DEFAULT_BM25_STATE_PATH = "data/index/bm25_state.json"


def bm25_state_path() -> Path:
    return Path(app_settings.bm25_state_path or DEFAULT_BM25_STATE_PATH)


def tokenize_bm25(text: str) -> list[str]:
    """Tokenize mixed Chinese/English text for sparse retrieval.

    The tokenizer intentionally stays deterministic and dependency-free:
    Chinese characters are indexed individually, while English words, API names,
    numbers, and versions are indexed as lowercase tokens.
    """

    tokens: list[str] = []
    for match in re.finditer(r"[\u4e00-\u9fff]|[A-Za-z0-9][A-Za-z0-9_.:/+-]*", text or ""):
        token = match.group(0).strip().lower()
        if token:
            tokens.append(token)
    return tokens


class BM25State:
    def __init__(self, path: Path | None = None):
        self.path = path or bm25_state_path()
        self.version = STATE_VERSION
        self.vocab: dict[str, int] = {}
        self.doc_freq: dict[str, int] = {}
        self.documents: dict[str, dict[str, Any]] = {}
        self.total_docs = 0
        self.sum_token_len = 0
        self.updated_at = ""

    @property
    def avg_doc_len(self) -> float:
        if self.total_docs <= 0:
            return 0.0
        return self.sum_token_len / self.total_docs

    @classmethod
    def load(cls, path: Path | None = None) -> "BM25State":
        state = cls(path=path)
        if not state.path.exists():
            return state
        try:
            data = json.loads(state.path.read_text(encoding="utf-8"))
        except Exception:
            return state
        state.version = int(data.get("version") or STATE_VERSION)
        state.vocab = {str(k): int(v) for k, v in (data.get("vocab") or {}).items()}
        state.doc_freq = {str(k): int(v) for k, v in (data.get("doc_freq") or {}).items()}
        state.documents = dict(data.get("documents") or {})
        state.total_docs = int(data.get("total_docs") or len(state.documents))
        state.sum_token_len = int(data.get("sum_token_len") or 0)
        state.updated_at = str(data.get("updated_at") or "")
        if not state.sum_token_len and state.documents:
            state.sum_token_len = sum(int(doc.get("token_len") or 0) for doc in state.documents.values())
        return state

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        payload = {
            "version": self.version,
            "updated_at": self.updated_at,
            "total_docs": self.total_docs,
            "sum_token_len": self.sum_token_len,
            "avg_doc_len": self.avg_doc_len,
            "vocab": self.vocab,
            "doc_freq": self.doc_freq,
            "documents": self.documents,
        }
        fd, temp_name = tempfile.mkstemp(prefix="bm25_state_", suffix=".json", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def rebuild_from_nodes(self, nodes: Iterable[TextNode]) -> None:
        self.vocab = {}
        self.doc_freq = {}
        self.documents = {}
        self.total_docs = 0
        self.sum_token_len = 0
        self.increment_add(nodes, save=False)
        self.save()

    def increment_add(self, nodes: Iterable[TextNode], *, save: bool = True) -> int:
        count = 0
        for node in nodes:
            doc = self._node_to_doc_record(node)
            if doc is None:
                continue
            chunk_id = doc["chunk_id"]
            if chunk_id in self.documents:
                self.increment_remove_by_chunk_ids([chunk_id], save=False)
            for token in doc["term_freq"]:
                if token not in self.vocab:
                    self.vocab[token] = len(self.vocab)
                self.doc_freq[token] = int(self.doc_freq.get(token, 0)) + 1
            self.documents[chunk_id] = doc
            self.total_docs += 1
            self.sum_token_len += int(doc["token_len"])
            count += 1
        if save and count:
            self.save()
        return count

    def increment_remove(self, nodes: Iterable[TextNode], *, save: bool = True) -> int:
        chunk_ids = []
        fallback_docs = []
        for node in nodes:
            chunk_id = self._chunk_id_for_node(node)
            if chunk_id:
                chunk_ids.append(chunk_id)
            else:
                doc = self._node_to_doc_record(node)
                if doc:
                    fallback_docs.append(doc)

        removed = self.increment_remove_by_chunk_ids(chunk_ids, save=False)
        for doc in fallback_docs:
            removed += self._remove_doc_record(doc)
        if save and removed:
            self.save()
        return removed

    def increment_remove_by_chunk_ids(self, chunk_ids: Iterable[str], *, save: bool = True) -> int:
        removed = 0
        for chunk_id in chunk_ids:
            doc = self.documents.pop(str(chunk_id), None)
            if not doc:
                continue
            removed += self._remove_doc_record(doc, already_popped=True)
        if save and removed:
            self.save()
        return removed

    def _remove_doc_record(self, doc: dict[str, Any], *, already_popped: bool = False) -> int:
        if not already_popped:
            self.documents.pop(str(doc.get("chunk_id") or ""), None)
        for token in doc.get("term_freq") or {}:
            next_df = int(self.doc_freq.get(token, 0)) - 1
            if next_df > 0:
                self.doc_freq[token] = next_df
            else:
                self.doc_freq.pop(token, None)
        self.total_docs = max(0, self.total_docs - 1)
        self.sum_token_len = max(0, self.sum_token_len - int(doc.get("token_len") or 0))
        return 1

    @staticmethod
    def _chunk_id_for_node(node: TextNode) -> str:
        metadata = dict(node.metadata or {})
        canonical = CanonicalChunkMetadata.from_raw({**metadata, "chunk_id": metadata.get("chunk_id") or node.node_id})
        return str(canonical.chunk_id or node.node_id or "")

    def _node_to_doc_record(self, node: TextNode) -> dict[str, Any] | None:
        text = (node.text or "").strip()
        if not text:
            return None
        chunk_id = self._chunk_id_for_node(node)
        if not chunk_id:
            return None
        tokens = tokenize_bm25(text)
        if not tokens:
            return None
        metadata = dict(node.metadata or {})
        canonical = CanonicalChunkMetadata.from_raw({**metadata, "chunk_id": chunk_id})
        term_freq = Counter(tokens)
        return {
            "chunk_id": chunk_id,
            "doc_id": canonical.doc_id,
            "token_len": len(tokens),
            "term_freq": dict(term_freq),
        }


class PersistentBM25Retriever(BaseRetriever):
    """BM25 retriever backed by persisted BM25State."""

    def __init__(
        self,
        *,
        nodes: list[TextNode],
        state: BM25State,
        similarity_top_k: int = 4,
        k1: float = 1.5,
        b: float = 0.75,
        **kwargs: Any,
    ):
        super().__init__(**kwargs)
        self.nodes_by_id = {BM25State._chunk_id_for_node(node): node for node in nodes}
        self.state = state
        self.similarity_top_k = similarity_top_k
        self.k1 = k1
        self.b = b

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        query_tokens = tokenize_bm25(query_bundle.query_str)
        if not query_tokens or self.state.total_docs <= 0:
            return []

        query_tf = Counter(query_tokens)
        avgdl = self.state.avg_doc_len or 1.0
        scores: list[tuple[str, float]] = []
        for chunk_id, doc in self.state.documents.items():
            term_freq = doc.get("term_freq") or {}
            doc_len = int(doc.get("token_len") or 0) or 1
            score = 0.0
            for token, query_count in query_tf.items():
                tf = float(term_freq.get(token) or 0)
                if tf <= 0:
                    continue
                df = max(1, int(self.state.doc_freq.get(token) or 0))
                idf = math.log(1 + (self.state.total_docs - df + 0.5) / (df + 0.5))
                denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / avgdl)
                score += query_count * idf * ((tf * (self.k1 + 1)) / denominator)
            if score > 0:
                scores.append((chunk_id, score))

        scores.sort(key=lambda item: item[1], reverse=True)
        results: list[NodeWithScore] = []
        for chunk_id, score in scores[: self.similarity_top_k]:
            node = self.nodes_by_id.get(chunk_id)
            if node is not None:
                results.append(NodeWithScore(node=node, score=score))
        return results


def build_stateful_bm25_retriever(
    *,
    nodes: list[TextNode],
    similarity_top_k: int,
) -> PersistentBM25Retriever:
    state = BM25State.load()
    if len(state.documents) != len(nodes):
        print(f"[BM25State] Rebuilding state: state_docs={len(state.documents)}, nodes={len(nodes)}")
        state.rebuild_from_nodes(nodes)
    else:
        print(f"[BM25State] Loaded state: docs={state.total_docs}, vocab={len(state.vocab)}")
    return PersistentBM25Retriever(
        nodes=nodes,
        state=state,
        similarity_top_k=similarity_top_k,
        k1=app_settings.bm25_k1,
        b=app_settings.bm25_b,
    )


def rebuild_bm25_state(nodes: Iterable[TextNode]) -> BM25State:
    state = BM25State.load()
    state.rebuild_from_nodes(nodes)
    return state


def increment_add_bm25_nodes(nodes: Iterable[TextNode]) -> int:
    state = BM25State.load()
    return state.increment_add(nodes)


def increment_remove_bm25_nodes(nodes: Iterable[TextNode]) -> int:
    state = BM25State.load()
    return state.increment_remove(nodes)
