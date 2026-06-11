# app/auth/repository.py
"""Data access layer for users and chat_sessions."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.storage.auth_models import (
    AuditLog,
    Base,
    ChatMessage,
    ChatSession,
    KnowledgeDocument,
    KnowledgeIngestJob,
    SessionSummary,
    User,
)


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.postgres_url, echo=False)
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal


def get_db():
    """FastAPI dependency that yields a DB session."""
    factory = get_session_factory()
    db = factory()
    try:
        yield db
    finally:
        db.close()


def init_tables():
    """Create auth tables if they don't exist."""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)


# ============================================================
# User CRUD
# ============================================================

def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: str) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def create_user(
    db: Session,
    user_id: str,
    username: str,
    password_hash: str,
    display_name: str,
    email: Optional[str] = None,
    role: str = "user",
) -> User:
    now = datetime.utcnow()
    user = User(
        id=user_id,
        username=username,
        email=email,
        display_name=display_name or username,
        password_hash=password_hash,
        role=role,
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def update_last_login(db: Session, user: User) -> None:
    user.last_login_at = datetime.utcnow()
    db.commit()


def is_users_table_empty(db: Session) -> bool:
    return db.query(User).count() == 0


def list_users(db: Session, keyword: str = "", role: str = "", status: str = "", limit: int = 100, offset: int = 0) -> tuple[list[User], int]:
    query = db.query(User)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter((User.username.ilike(like)) | (User.display_name.ilike(like)) | (User.email.ilike(like)))
    if role:
        query = query.filter(User.role == role)
    if status:
        query = query.filter(User.status == status)
    total = query.count()
    users = query.order_by(User.created_at.desc()).offset(offset).limit(limit).all()
    return users, total


def update_user(
    db: Session,
    user: User,
    *,
    display_name: Optional[str] = None,
    email: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
) -> User:
    if display_name is not None:
        user.display_name = display_name
    if email is not None:
        user.email = email or None
    if role is not None:
        user.role = role
    if status is not None:
        user.status = status
    user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return user


# ============================================================
# ChatSession CRUD
# ============================================================

def get_chat_session(db: Session, session_id: str) -> Optional[ChatSession]:
    return db.query(ChatSession).filter(ChatSession.session_id == session_id).first()


def create_chat_session(
    db: Session,
    session_id: str,
    owner_user_id: str,
    title: Optional[str] = None,
) -> ChatSession:
    now = datetime.utcnow()
    session = ChatSession(
        session_id=session_id,
        owner_user_id=owner_user_id,
        title=title,
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def list_user_sessions(db: Session, user_id: str, limit: int = 50) -> list[ChatSession]:
    return (
        db.query(ChatSession)
        .filter(ChatSession.owner_user_id == user_id, ChatSession.status == "active")
        .order_by(ChatSession.last_message_at.desc().nullslast(), ChatSession.created_at.desc())
        .limit(limit)
        .all()
    )


def update_session_last_message(db: Session, session: ChatSession) -> None:
    session.last_message_at = datetime.utcnow()
    session.updated_at = datetime.utcnow()
    db.commit()


def soft_delete_session(db: Session, session: ChatSession) -> None:
    session.status = "deleted"
    session.updated_at = datetime.utcnow()
    db.commit()


def create_chat_exchange(
    db: Session,
    *,
    session_id: str,
    owner_user_id: str,
    user_content: str,
    assistant_content: str,
    assistant_status: str = "completed",
    assistant_metadata: Optional[dict] = None,
) -> tuple[ChatMessage, ChatMessage]:
    """Persist one user/assistant exchange as the complete history source."""

    user_message = ChatMessage(
        session_id=session_id,
        owner_user_id=owner_user_id,
        role="user",
        content=user_content,
        status="completed",
        metadata_json={},
    )
    assistant_message = ChatMessage(
        session_id=session_id,
        owner_user_id=owner_user_id,
        role="assistant",
        content=assistant_content,
        status=assistant_status,
        metadata_json=assistant_metadata or {},
    )
    db.add(user_message)
    db.add(assistant_message)
    db.commit()
    db.refresh(user_message)
    db.refresh(assistant_message)
    return user_message, assistant_message


def list_chat_messages(db: Session, session_id: str) -> list[ChatMessage]:
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.asc())
        .all()
    )


def list_recent_chat_messages(db: Session, session_id: str, limit: int) -> list[ChatMessage]:
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(messages))


def get_session_summary(db: Session, session_id: str) -> Optional[SessionSummary]:
    return db.query(SessionSummary).filter(SessionSummary.session_id == session_id).first()


def upsert_session_summary(
    db: Session,
    *,
    session_id: str,
    owner_user_id: str,
    summary: str,
    covered_until_message_id: Optional[int],
    covered_message_count: int,
) -> SessionSummary:
    row = get_session_summary(db, session_id)
    now = datetime.utcnow()
    if row is None:
        row = SessionSummary(
            session_id=session_id,
            owner_user_id=owner_user_id,
            created_at=now,
        )
        db.add(row)
    else:
        row.version += 1
    row.summary = summary
    row.covered_until_message_id = covered_until_message_id
    row.covered_message_count = covered_message_count
    row.updated_at = now
    db.commit()
    db.refresh(row)
    return row


# ============================================================
# Knowledge Base CRUD
# ============================================================

def get_knowledge_document_by_relative_path(db: Session, relative_path: str) -> Optional[KnowledgeDocument]:
    return (
        db.query(KnowledgeDocument)
        .filter(KnowledgeDocument.relative_path == relative_path)
        .first()
    )


def list_knowledge_documents(
    db: Session,
    keyword: str = "",
    status: str = "active",
    file_type: str = "",
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[KnowledgeDocument], int]:
    query = db.query(KnowledgeDocument)
    if status:
        query = query.filter(KnowledgeDocument.status == status)
    if file_type:
        query = query.filter(KnowledgeDocument.file_type == file_type)
    if keyword:
        like = f"%{keyword}%"
        query = query.filter(
            (KnowledgeDocument.filename.ilike(like))
            | (KnowledgeDocument.relative_path.ilike(like))
            | (KnowledgeDocument.doc_id.ilike(like))
        )
    total = query.count()
    rows = query.order_by(KnowledgeDocument.updated_at.desc()).offset(offset).limit(limit).all()
    return rows, total


def upsert_knowledge_document(
    db: Session,
    *,
    relative_path: str,
    filename: str,
    file_type: str,
    file_size: int,
    sha256: Optional[str] = None,
    doc_id: Optional[str] = None,
    indexed_status: str = "pending",
    uploaded_by: Optional[str] = None,
    last_ingest_job_id: Optional[str] = None,
    metadata_json: Optional[dict] = None,
    last_ingested_at: Optional[datetime] = None,
) -> KnowledgeDocument:
    row = get_knowledge_document_by_relative_path(db, relative_path)
    now = datetime.utcnow()
    if row is None:
        doc_pk = hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:32]
        row = KnowledgeDocument(
            id=f"doc_{doc_pk}",
            relative_path=relative_path,
            created_at=now,
        )
        db.add(row)
    row.filename = filename
    row.file_type = file_type
    row.file_size = int(file_size or 0)
    row.sha256 = sha256
    row.doc_id = doc_id
    row.status = "active"
    row.indexed_status = indexed_status
    if uploaded_by is not None:
        row.uploaded_by = uploaded_by
    if last_ingest_job_id is not None:
        row.last_ingest_job_id = last_ingest_job_id
    row.metadata_json = metadata_json or {}
    row.updated_at = now
    if last_ingested_at is not None:
        row.last_ingested_at = last_ingested_at
    db.commit()
    db.refresh(row)
    return row


def mark_knowledge_document_deleted(db: Session, relative_path: str, job_id: Optional[str] = None) -> Optional[KnowledgeDocument]:
    row = get_knowledge_document_by_relative_path(db, relative_path)
    if row is None:
        return None
    row.status = "deleted"
    row.indexed_status = "pending"
    if job_id is not None:
        row.last_ingest_job_id = job_id
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


def get_knowledge_job(db: Session, job_id: str) -> Optional[KnowledgeIngestJob]:
    return db.query(KnowledgeIngestJob).filter(KnowledgeIngestJob.id == job_id).first()


def create_knowledge_job(
    db: Session,
    *,
    job_id: str,
    trigger_type: str,
    created_by: Optional[str],
    saved_files: list[dict],
) -> KnowledgeIngestJob:
    now = datetime.utcnow()
    row = KnowledgeIngestJob(
        id=job_id,
        status="pending",
        trigger_type=trigger_type,
        created_by=created_by,
        saved_files=saved_files,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_knowledge_job(
    db: Session,
    job_id: str,
    *,
    status: Optional[str] = None,
    result_json: Optional[dict] = None,
    report_json: Optional[dict] = None,
    error: Optional[str] = None,
) -> Optional[KnowledgeIngestJob]:
    row = get_knowledge_job(db, job_id)
    if row is None:
        return None
    now = datetime.utcnow()
    if status is not None:
        row.status = status
        if status == "running" and row.started_at is None:
            row.started_at = now
        if status in {"succeeded", "failed"}:
            row.finished_at = now
    if result_json is not None:
        row.result_json = result_json
    if report_json is not None:
        row.report_json = report_json
    if error is not None:
        row.error = error
    row.updated_at = now
    db.commit()
    db.refresh(row)
    return row


def list_knowledge_jobs(db: Session, limit: int = 20) -> list[KnowledgeIngestJob]:
    return (
        db.query(KnowledgeIngestJob)
        .order_by(KnowledgeIngestJob.created_at.desc())
        .limit(limit)
        .all()
    )


# ============================================================
# Audit Log CRUD
# ============================================================

def create_audit_log(
    db: Session,
    *,
    actor_user_id: Optional[str],
    actor_username: Optional[str],
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    resource_name: Optional[str] = None,
    status: str = "succeeded",
    detail_json: Optional[dict] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    row = AuditLog(
        id=f"aud_{uuid.uuid4().hex[:24]}",
        actor_user_id=actor_user_id,
        actor_username=actor_username,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        status=status,
        detail_json=detail_json or {},
        ip_address=ip_address,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_audit_logs(
    db: Session,
    *,
    action: str = "",
    actor: str = "",
    resource_type: str = "",
    status: str = "",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    query = db.query(AuditLog)
    if action:
        query = query.filter(AuditLog.action == action)
    if resource_type:
        query = query.filter(AuditLog.resource_type == resource_type)
    if status:
        query = query.filter(AuditLog.status == status)
    if actor:
        like = f"%{actor}%"
        query = query.filter((AuditLog.actor_username.ilike(like)) | (AuditLog.actor_user_id.ilike(like)))
    total = query.count()
    rows = query.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit).all()
    return rows, total
