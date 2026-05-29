"""PostgreSQL-backed hierarchical chunk node store.

Milvus stores embeddings for leaf nodes. PostgreSQL stores root, parent, and
leaf node text plus metadata so retrieval can expand a matched leaf to its
parent context.
"""

from __future__ import annotations

from typing import Iterable, Optional

from llama_index.core.schema import TextNode
from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, Text, create_engine, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from app.metadata_schema import CanonicalChunkMetadata


metadata = MetaData()

chunk_nodes = Table(
    "rag_chunk_nodes",
    metadata,
    Column("chunk_id", String(512), primary_key=True),
    Column("doc_id", String(512), nullable=False, index=True),
    Column("source_path", Text, nullable=False, default=""),
    Column("section_id", String(512), nullable=False, default="", index=True),
    Column("parent_id", String(512), nullable=False, default="", index=True),
    Column("chunk_index", Integer, nullable=False, default=0),
    Column("page_range", String(128), nullable=False, default=""),
    Column("text", Text, nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
)


class ParentStore:
    def __init__(self, database_url: str):
        self.engine: Engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
        )

    def create_schema(self) -> None:
        metadata.create_all(self.engine)

    def delete_by_doc_ids(self, doc_ids: Iterable[str]) -> None:
        doc_ids = [doc_id for doc_id in doc_ids if doc_id]
        if not doc_ids:
            return
        with self.engine.begin() as conn:
            conn.execute(delete(chunk_nodes).where(chunk_nodes.c.doc_id.in_(doc_ids)))

    def get_parent(self, parent_id: str) -> Optional[dict]:
        node = self.get_node(parent_id)
        if not node:
            return None
        return {
            "parent_id": node["chunk_id"],
            "doc_id": node["doc_id"],
            "source_path": node["source_path"],
            "section_title": CanonicalChunkMetadata.from_raw(node.get("metadata_json") or {}).section_title,
            "section_index": node["chunk_index"],
            "page_range": node["page_range"],
            "text": node["text"],
            "metadata_json": node["metadata_json"],
        }

    def get_node(self, chunk_id: str) -> Optional[dict]:
        if not chunk_id:
            return None
        stmt = select(chunk_nodes).where(chunk_nodes.c.chunk_id == chunk_id)
        with self.engine.begin() as conn:
            node_row = conn.execute(stmt).mappings().first()
        return dict(node_row) if node_row else None

    def list_children(self, parent_id: str, leaf_only: bool = True) -> list[dict]:
        if not parent_id:
            return []
        stmt = select(chunk_nodes).where(chunk_nodes.c.parent_id == parent_id)
        stmt = stmt.order_by(chunk_nodes.c.chunk_index)
        with self.engine.begin() as conn:
            rows = conn.execute(stmt).mappings().all()

        children = []
        for row in rows:
            row_dict = dict(row)
            metadata_dict = dict(row_dict.get("metadata_json") or {})
            if leaf_only and metadata_dict.get("chunk_role") not in (None, "", "leaf"):
                continue
            children.append(row_dict)
        return children

    def count_children(self, parent_id: str, leaf_only: bool = True) -> int:
        return len(self.list_children(parent_id=parent_id, leaf_only=leaf_only))

    def upsert_nodes(self, nodes: Iterable[TextNode], batch_size: int = 500) -> int:
        rows_by_chunk_id = {}
        for node in nodes:
            row = self._node_to_row(node)
            if row is not None:
                rows_by_chunk_id[row["chunk_id"]] = row

        rows = list(rows_by_chunk_id.values())
        if not rows:
            return 0

        total = 0
        for start in range(0, len(rows), batch_size):
            total += self._upsert_node_rows(rows[start : start + batch_size])
        return total

    def _upsert_node_rows(self, rows: list[dict]) -> int:
        stmt = pg_insert(chunk_nodes).values(rows)
        update_columns = {
            "doc_id": stmt.excluded.doc_id,
            "source_path": stmt.excluded.source_path,
            "section_id": stmt.excluded.section_id,
            "parent_id": stmt.excluded.parent_id,
            "chunk_index": stmt.excluded.chunk_index,
            "page_range": stmt.excluded.page_range,
            "text": stmt.excluded.text,
            "metadata_json": stmt.excluded.metadata_json,
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[chunk_nodes.c.chunk_id],
            set_=update_columns,
        )

        with self.engine.begin() as conn:
            conn.execute(stmt)
        return len(rows)

    def list_chunk_nodes(self, doc_ids: Optional[Iterable[str]] = None, leaf_only: bool = True) -> list[TextNode]:
        stmt = select(chunk_nodes)
        if doc_ids is not None:
            doc_ids = [doc_id for doc_id in doc_ids if doc_id]
            if not doc_ids:
                return []
            stmt = stmt.where(chunk_nodes.c.doc_id.in_(doc_ids))
        stmt = stmt.order_by(chunk_nodes.c.doc_id, chunk_nodes.c.chunk_index)

        with self.engine.begin() as conn:
            rows = conn.execute(stmt).mappings().all()

        nodes: list[TextNode] = []
        for row in rows:
            metadata_dict = dict(row.get("metadata_json") or {})
            if leaf_only and metadata_dict.get("chunk_role") not in (None, "", "leaf"):
                continue
            canonical = CanonicalChunkMetadata.from_raw(
                {
                    **metadata_dict,
                    "chunk_id": row["chunk_id"],
                    "doc_id": row["doc_id"],
                    "source_path": row["source_path"],
                    "parent_id": row["parent_id"],
                    "page_range": row["page_range"],
                }
            )
            metadata_dict.update(canonical.model_dump())
            metadata_dict.setdefault("chunk_index", row["chunk_index"])
            nodes.append(
                TextNode(
                    id_=str(row["chunk_id"]),
                    text=str(row["text"] or ""),
                    metadata=metadata_dict,
                )
            )
        return nodes

    @staticmethod
    def _node_to_row(node: TextNode) -> Optional[dict]:
        metadata_dict = dict(node.metadata or {})
        canonical = CanonicalChunkMetadata.from_raw({**metadata_dict, "chunk_id": metadata_dict.get("chunk_id") or node.node_id})
        chunk_id = canonical.chunk_id or node.node_id
        doc_id = canonical.doc_id
        text = (node.text or "").strip()

        if not chunk_id or not doc_id or not text:
            return None

        return {
            "chunk_id": str(chunk_id),
            "doc_id": str(doc_id),
            "source_path": canonical.source_path,
            "section_id": canonical.section_id,
            "parent_id": canonical.parent_id,
            "chunk_index": int(metadata_dict.get("chunk_index") or 0),
            "page_range": canonical.page_range,
            "text": text,
            "metadata_json": metadata_dict,
        }


def build_parent_store(database_url: str) -> ParentStore:
    store = ParentStore(database_url)
    store.create_schema()
    return store
