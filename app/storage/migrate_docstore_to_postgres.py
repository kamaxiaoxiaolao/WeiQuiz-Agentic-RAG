"""Migrate existing LlamaIndex docstore nodes into PostgreSQL chunk store."""

from __future__ import annotations

import argparse

from llama_index.core import StorageContext

from app.config import settings
from app.storage.parent_store import build_parent_store


def migrate_docstore(index_dir: str) -> int:
    storage_context = StorageContext.from_defaults(persist_dir=index_dir)
    nodes = list(storage_context.docstore.docs.values())
    store = build_parent_store(settings.postgres_url)
    return store.upsert_nodes(nodes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate local docstore.json TextNodes into PostgreSQL.")
    parser.add_argument("--index-dir", default=settings.index_dir, help="LlamaIndex persisted index directory.")
    args = parser.parse_args()

    count = migrate_docstore(args.index_dir)
    print("--- Docstore Migration Completed ---")
    print(f"index_dir: {args.index_dir}")
    print(f"chunk_nodes_upserted: {count}")


if __name__ == "__main__":
    main()
