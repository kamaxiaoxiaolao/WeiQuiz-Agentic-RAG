"""Knowledge document management routes."""

from __future__ import annotations

from pathlib import Path
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api_support.helpers import (
    _create_ingest_job,
    _docs_root,
    _library_payload,
    _list_ingest_jobs,
    _load_ingest_job,
    _redis_or_503,
    _safe_docs_relative_path,
    _safe_upload_filename,
    _save_upload_file,
    _serialize_knowledge_job,
    _write_audit_log,
)
from app.api_support.state import get_app_state
from app.auth.dependencies import get_current_user, require_admin
from app.auth.repository import (
    get_db,
    list_knowledge_jobs,
    mark_knowledge_document_deleted,
    upsert_knowledge_document,
)
from app.storage.auth_models import User


router = APIRouter()


@router.post("/documents/upload")
async def upload_documents(
    http_request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not files:
        raise HTTPException(status_code=400, detail="at least one file is required")

    r = _redis_or_503()
    saved_files = []
    for file in files:
        filename = _safe_upload_filename(file.filename or "")
        content = await file.read()
        saved_files.append(await run_in_threadpool(_save_upload_file, filename, content))

    job_id = _create_ingest_job(
        r,
        saved_files,
        background_tasks,
        trigger_type="upload",
        created_by=admin.id,
        db=db,
    )
    for item in saved_files:
        relative_path = Path(item["path"]).name
        upsert_knowledge_document(
            db,
            relative_path=relative_path,
            filename=item["file_name"],
            file_type=Path(item["file_name"]).suffix.lower().lstrip("."),
            file_size=item["file_size"],
            indexed_status="pending",
            uploaded_by=admin.id,
            last_ingest_job_id=job_id,
            metadata_json={"source": "upload"},
        )

    _write_audit_log(
        db,
        actor=admin,
        action="knowledge.upload",
        resource_type="knowledge_job",
        resource_id=job_id,
        resource_name="upload documents",
        detail={
            "saved_files": [
                {"file_name": item.get("file_name"), "file_size": item.get("file_size")}
                for item in saved_files
            ],
        },
        request=http_request,
    )
    return {
        "ok": True,
        "job_id": job_id,
        "status": "pending",
        "saved_files": saved_files,
    }


@router.get("/documents/library")
async def get_document_library(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    r = getattr(get_app_state(), "redis", None)
    payload = _library_payload(r, db=db)
    can_manage = current_user.role == "admin"
    payload["permissions"] = {
        "can_read": True,
        "can_manage": can_manage,
        "can_upload": can_manage,
        "can_delete": can_manage,
        "can_reindex": can_manage,
        "can_view_jobs": can_manage,
    }
    if not can_manage:
        payload["jobs"] = []
        payload["report"] = None
        for doc in payload.get("documents") or []:
            doc.pop("metadata", None)
            doc.pop("uploaded_by", None)
            doc.pop("last_ingest_job_id", None)
    return payload


@router.get("/documents/jobs")
async def list_document_jobs(
    limit: int = 20,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    r = _redis_or_503()
    db_jobs = [_serialize_knowledge_job(row) for row in list_knowledge_jobs(db, max(1, min(limit, 100)))]
    return {"jobs": db_jobs or _list_ingest_jobs(r, max(1, min(limit, 100)))}


@router.get("/documents/jobs/{job_id}")
async def get_ingest_job(
    job_id: str,
    admin: User = Depends(require_admin),
):
    r = _redis_or_503()
    job = _load_ingest_job(r, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="ingestion job not found")
    return job


@router.post("/documents/reindex")
async def reindex_documents(
    http_request: Request,
    background_tasks: BackgroundTasks,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    r = _redis_or_503()
    job_id = _create_ingest_job(
        r,
        [],
        background_tasks,
        trigger_type="reindex",
        created_by=admin.id,
        db=db,
    )
    _write_audit_log(
        db,
        actor=admin,
        action="knowledge.reindex",
        resource_type="knowledge_job",
        resource_id=job_id,
        resource_name="reindex",
        detail={},
        request=http_request,
    )
    return {"ok": True, "job_id": job_id, "status": "pending"}


@router.delete("/documents/files/{document_path:path}")
async def delete_document_file(
    http_request: Request,
    document_path: str,
    background_tasks: BackgroundTasks,
    reindex: bool = True,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = _safe_docs_relative_path(document_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="document not found")

    file_info = {
        "file_name": target.name,
        "relative_path": target.relative_to(_docs_root()).as_posix(),
        "path": str(target).replace("\\", "/"),
        "file_size": target.stat().st_size,
    }
    target.unlink()

    job_id = None
    if reindex:
        r = _redis_or_503()
        job_id = _create_ingest_job(
            r,
            [],
            background_tasks,
            trigger_type="delete",
            created_by=admin.id,
            db=db,
        )
    mark_knowledge_document_deleted(db, file_info["relative_path"], job_id=job_id)
    _write_audit_log(
        db,
        actor=admin,
        action="knowledge.delete",
        resource_type="knowledge_document",
        resource_id=file_info["relative_path"],
        resource_name=file_info["file_name"],
        detail={"deleted": file_info, "reindex_job_id": job_id},
        request=http_request,
    )

    return {"ok": True, "deleted": file_info, "reindex_job_id": job_id}
