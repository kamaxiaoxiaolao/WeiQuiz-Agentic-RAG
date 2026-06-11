import os
import json
import hashlib
import re
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def compute_sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state(state_path: str) -> dict:
    if not os.path.exists(state_path):
        return {"version": 1, "docs": {}}
    try:
        if os.path.getsize(state_path) == 0:
            return {"version": 1, "docs": {}}
        with open(state_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": 1, "docs": {}}
        if "docs" not in data or not isinstance(data["docs"], dict):
            return {"version": 1, "docs": {}}
        return data
    except Exception:
        return {"version": 1, "docs": {}}


def save_state(state_path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def normalize_rel_path(path: str) -> str:
    return path.replace("\\", "/")


def make_doc_id_from_rel_path(rel_path: str) -> str:
    name = normalize_rel_path(rel_path).strip().lower()
    name = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", name)
    name = name.strip("_")
    return name or "document"


def timestamp_to_utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat()


def build_file_metadata(abs_path: str, rel_path: str) -> dict:
    _, ext = os.path.splitext(abs_path)
    stat = os.stat(abs_path)
    return {
        "source_path": rel_path,
        "file_name": os.path.basename(abs_path),
        "file_type": ext.lower(),
        "file_size": stat.st_size,
        "mtime": timestamp_to_utc_iso(stat.st_mtime),
    }


def iter_files(root_dir: str, exts: Optional[List[str]] = None):
    exts = [e.lower() for e in (exts or [])]
    for root, _, files in os.walk(root_dir):
        for name in files:
            if exts:
                _, ext = os.path.splitext(name)
                if ext.lower() not in exts:
                    continue
            yield os.path.join(root, name)


def parse_doc_id_from_front_matter(file_path: str) -> Optional[str]:
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            first = f.readline().strip()
            if first != "---":
                return None
            for _ in range(80):
                line = f.readline()
                if not line:
                    return None
                s = line.strip()
                if s == "---":
                    return None
                if s.startswith("doc_id:"):
                    return s.split(":", 1)[1].strip() or None
    except Exception:
        return None
    return None


def diff_docs(docs_dir: str, state: dict) -> Tuple[Dict, Dict]:
    old_docs: Dict[str, dict] = state.get("docs", {})
    now = utc_now_iso()
    current: Dict[str, dict] = {}
    for abs_path in iter_files(docs_dir, exts=[".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm"]):
        rel_path = normalize_rel_path(os.path.relpath(abs_path, "."))
        docs_rel_path = normalize_rel_path(os.path.relpath(abs_path, docs_dir))
        sha = compute_sha256(abs_path)
        doc_id = parse_doc_id_from_front_matter(abs_path) or make_doc_id_from_rel_path(docs_rel_path)
        file_metadata = build_file_metadata(abs_path, rel_path)
        current[rel_path] = {
            **file_metadata,
            "sha256": sha,
            "doc_id": doc_id,
            "updated_at": old_docs.get(rel_path, {}).get("updated_at", now),
            "last_seen_at": now,
        }

    added: List[dict] = []
    updated: List[dict] = []
    deleted: List[dict] = []

    for rel_path, cur in current.items():
        if rel_path not in old_docs:
            added.append(
                {
                    "path": rel_path,
                    "doc_id": cur["doc_id"],
                    "sha256": cur["sha256"],
                    "source_path": cur["source_path"],
                    "file_name": cur["file_name"],
                    "file_type": cur["file_type"],
                    "file_size": cur["file_size"],
                    "mtime": cur["mtime"],
                }
            )
            continue

        old = old_docs[rel_path]
        old_sha = old.get("sha256")
        if old_sha != cur["sha256"]:
            cur["updated_at"] = now
            updated.append(
                {
                    "path": rel_path,
                    "doc_id": cur["doc_id"],
                    "old_sha256": old_sha,
                    "new_sha256": cur["sha256"],
                    "source_path": cur["source_path"],
                    "file_name": cur["file_name"],
                    "file_type": cur["file_type"],
                    "file_size": cur["file_size"],
                    "mtime": cur["mtime"],
                }
            )
        else:
            cur["updated_at"] = old.get("updated_at", now)

    for rel_path, old in old_docs.items():
        if rel_path not in current:
            deleted.append(
                {
                    "path": rel_path,
                    "doc_id": old.get("doc_id", os.path.basename(rel_path)),
                    "source_path": old.get("source_path", rel_path),
                    "file_name": old.get("file_name", os.path.basename(rel_path)),
                    "file_type": old.get("file_type", os.path.splitext(rel_path)[1].lower()),
                    "file_size": old.get("file_size"),
                    "mtime": old.get("mtime"),
                    "sha256": old.get("sha256"),
                }
            )

    diff = {"added": added, "updated": updated, "deleted": deleted}
    next_state = {"version": 1, "docs": current}
    return diff, next_state


if __name__ == "__main__":
    DOCS_DIR = "data/docs"
    STATE_PATH = "data/index/ingest_state.json"

    state = load_state(STATE_PATH)
    diff, next_state = diff_docs(DOCS_DIR, state)

    print("新增:", diff["added"])
    print("更新:", diff["updated"])
    print("删除:", diff["deleted"])

    print("Preview only: state is not saved. Run app.ingest.milvus_loader to apply changes.")
