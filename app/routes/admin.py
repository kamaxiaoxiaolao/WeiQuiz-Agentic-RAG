"""Administrative user and audit routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api_support.helpers import (
    _audit_payload,
    _serialize_knowledge_job,
    _sync_knowledge_documents_from_filesystem,
    _user_payload,
    _write_audit_log,
)
from app.api_support.schemas import AdminUserCreateRequest, AdminUserUpdateRequest
from app.auth.dependencies import require_admin
from app.auth.repository import (
    create_user,
    get_db,
    get_user_by_email,
    get_user_by_id,
    get_user_by_username,
    list_audit_logs,
    list_knowledge_documents,
    list_knowledge_jobs,
    list_users,
    update_user,
)
from app.auth.security import hash_password
from app.storage.auth_models import User


router = APIRouter()


@router.get("/admin/users")
async def admin_list_users(
    keyword: str = "",
    role: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 50,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    safe_page = max(1, page)
    safe_page_size = max(1, min(page_size, 100))
    users, total = list_users(
        db,
        keyword=keyword.strip(),
        role=role.strip(),
        status=status.strip(),
        limit=safe_page_size,
        offset=(safe_page - 1) * safe_page_size,
    )
    return {
        "users": [_user_payload(user) for user in users],
        "total": total,
        "page": safe_page,
        "page_size": safe_page_size,
    }


@router.post("/admin/users")
async def admin_create_user(
    http_request: Request,
    request: AdminUserCreateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    username = request.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="username is required")
    if len(request.password or "") < 6:
        raise HTTPException(status_code=400, detail="password must be at least 6 characters")
    if request.role not in {"admin", "user"}:
        raise HTTPException(status_code=400, detail="role must be admin or user")
    if get_user_by_username(db, username):
        raise HTTPException(status_code=409, detail="username already exists")
    email = (request.email or "").strip() or None
    if email and get_user_by_email(db, email):
        raise HTTPException(status_code=409, detail="email already exists")

    user = create_user(
        db=db,
        user_id=f"usr_{uuid.uuid4().hex[:16]}",
        username=username,
        password_hash=hash_password(request.password),
        display_name=request.display_name or username,
        email=email,
        role=request.role,
    )
    _write_audit_log(
        db,
        actor=admin,
        action="user.create",
        resource_type="user",
        resource_id=user.id,
        resource_name=user.username,
        detail={"role": user.role, "status": user.status, "email": user.email},
        request=http_request,
    )
    return _user_payload(user)


@router.get("/admin/audit-logs")
async def admin_list_audit_logs(
    action: str = "",
    actor: str = "",
    resource_type: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 50,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    safe_page = max(1, page)
    safe_page_size = max(1, min(page_size, 100))
    rows, total = list_audit_logs(
        db,
        action=action.strip(),
        actor=actor.strip(),
        resource_type=resource_type.strip(),
        status=status.strip(),
        limit=safe_page_size,
        offset=(safe_page - 1) * safe_page_size,
    )
    return {
        "logs": [_audit_payload(row) for row in rows],
        "total": total,
        "page": safe_page,
        "page_size": safe_page_size,
    }


@router.get("/admin/overview")
async def admin_overview(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _, total_users = list_users(db, limit=1)
    _, admin_users = list_users(db, role="admin", limit=1)
    _, disabled_users = list_users(db, status="disabled", limit=1)

    _sync_knowledge_documents_from_filesystem(db)
    documents, total_documents = list_knowledge_documents(db, status="active", limit=10000)
    jobs = list_knowledge_jobs(db, 8)
    failed_jobs = sum(1 for job in jobs if job.status == "failed")
    running_jobs = sum(1 for job in jobs if job.status in {"pending", "running"})

    return {
        "users": {
            "total": total_users,
            "admins": admin_users,
            "disabled": disabled_users,
            "active": max(0, total_users - disabled_users),
        },
        "knowledge": {
            "total_documents": total_documents,
            "indexed_documents": sum(1 for doc in documents if doc.indexed_status == "indexed"),
            "pending_documents": sum(1 for doc in documents if doc.indexed_status != "indexed"),
            "total_size": sum(int(doc.file_size or 0) for doc in documents),
            "file_types": sorted({str(doc.file_type or "").lstrip(".") for doc in documents if doc.file_type}),
        },
        "jobs": {
            "recent_total": len(jobs),
            "running": running_jobs,
            "failed": failed_jobs,
            "latest": [_serialize_knowledge_job(job) for job in jobs],
        },
    }


@router.put("/admin/users/{user_id}")
async def admin_update_user(
    http_request: Request,
    user_id: str,
    request: AdminUserUpdateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    role = request.role
    if role is not None and role not in {"admin", "user"}:
        raise HTTPException(status_code=400, detail="role must be admin or user")

    user_status = request.status
    if user_status is not None and user_status not in {"active", "disabled"}:
        raise HTTPException(status_code=400, detail="status must be active or disabled")

    if user.id == admin.id and user_status == "disabled":
        raise HTTPException(status_code=400, detail="cannot disable yourself")
    if user.id == admin.id and role == "user":
        raise HTTPException(status_code=400, detail="cannot remove your own admin role")

    before = {
        "display_name": user.display_name,
        "email": user.email,
        "role": user.role,
        "status": user.status,
    }
    updated = update_user(
        db,
        user,
        display_name=request.display_name,
        email=request.email,
        role=role,
        status=user_status,
    )
    after = {
        "display_name": updated.display_name,
        "email": updated.email,
        "role": updated.role,
        "status": updated.status,
    }
    changed = {key: {"before": before[key], "after": after[key]} for key in before if before[key] != after[key]}
    _write_audit_log(
        db,
        actor=admin,
        action="user.update",
        resource_type="user",
        resource_id=updated.id,
        resource_name=updated.username,
        detail={"changed": changed},
        request=http_request,
    )
    return _user_payload(updated)
