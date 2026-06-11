# app/storage/auth_models.py
"""SQLAlchemy models for authentication."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, String, Text, DateTime, CheckConstraint, Index, Integer, JSON
)
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id = Column(String(64), primary_key=True)
    username = Column(String(64), nullable=False)
    email = Column(String(128), nullable=True)
    display_name = Column(String(128), nullable=False)
    password_hash = Column(Text, nullable=False)
    role = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
    last_login_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_users_username", username, unique=True),
        Index("idx_users_email", email, unique=True),
        CheckConstraint("role IN ('admin', 'user')", name="chk_users_role"),
        CheckConstraint("status IN ('active', 'disabled')", name="chk_users_status"),
    )


class ChatSession(Base):
    """会话归属表"""
    __tablename__ = "chat_sessions"

    session_id = Column(String(64), primary_key=True)
    owner_user_id = Column(String(64), nullable=False)
    title = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="active")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
    last_message_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_chat_sessions_owner", owner_user_id),
        CheckConstraint("status IN ('active', 'deleted')", name="chk_chat_sessions_status"),
    )


class ChatMessage(Base):
    """Full persisted chat message history."""

    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False)
    owner_user_id = Column(String(64), nullable=False)
    role = Column(String(32), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="completed")
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_chat_messages_session_id", session_id),
        Index("idx_chat_messages_owner_session", owner_user_id, session_id),
        CheckConstraint("role IN ('user', 'assistant')", name="chk_chat_messages_role"),
    )


class SessionSummary(Base):
    """Rolling summary used for prompt memory compression."""

    __tablename__ = "session_summaries"

    session_id = Column(String(64), primary_key=True)
    owner_user_id = Column(String(64), nullable=False)
    summary = Column(Text, nullable=False, default="")
    covered_until_message_id = Column(Integer, nullable=True)
    covered_message_count = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_session_summaries_owner", owner_user_id),
    )


class KnowledgeDocument(Base):
    """Managed knowledge-base document metadata."""

    __tablename__ = "knowledge_documents"

    id = Column(String(128), primary_key=True)
    relative_path = Column(Text, nullable=False)
    filename = Column(String(512), nullable=False)
    file_type = Column(String(32), nullable=False)
    file_size = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(128), nullable=True)
    doc_id = Column(String(512), nullable=True)
    status = Column(String(32), nullable=False, default="active")
    indexed_status = Column(String(32), nullable=False, default="pending")
    chunk_count = Column(Integer, nullable=False, default=0)
    token_count = Column(Integer, nullable=False, default=0)
    uploaded_by = Column(String(64), nullable=True)
    last_ingest_job_id = Column(String(64), nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
    last_ingested_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_kb_documents_relative_path", relative_path, unique=True),
        Index("idx_kb_documents_status", status),
        Index("idx_kb_documents_file_type", file_type),
        CheckConstraint("status IN ('active', 'deleted')", name="chk_kb_documents_status"),
        CheckConstraint(
            "indexed_status IN ('pending', 'indexed', 'failed')",
            name="chk_kb_documents_indexed_status",
        ),
    )


class KnowledgeIngestJob(Base):
    """Persistent knowledge-base ingestion job audit record."""

    __tablename__ = "knowledge_ingest_jobs"

    id = Column(String(64), primary_key=True)
    status = Column(String(32), nullable=False, default="pending")
    trigger_type = Column(String(32), nullable=False, default="upload")
    created_by = Column(String(64), nullable=True)
    saved_files = Column(JSON, nullable=False, default=list)
    result_json = Column(JSON, nullable=True)
    report_json = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)
    started_at = Column(TIMESTAMP(timezone=True), nullable=True)
    finished_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_kb_ingest_jobs_status", status),
        Index("idx_kb_ingest_jobs_created_at", created_at),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="chk_kb_ingest_jobs_status",
        ),
        CheckConstraint(
            "trigger_type IN ('upload', 'reindex', 'delete', 'sync')",
            name="chk_kb_ingest_jobs_trigger_type",
        ),
    )


class AuditLog(Base):
    """Administrative operation audit log."""

    __tablename__ = "audit_logs"

    id = Column(String(64), primary_key=True)
    actor_user_id = Column(String(64), nullable=True)
    actor_username = Column(String(64), nullable=True)
    action = Column(String(128), nullable=False)
    resource_type = Column(String(64), nullable=False)
    resource_id = Column(String(512), nullable=True)
    resource_name = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="succeeded")
    detail_json = Column(JSON, nullable=False, default=dict)
    ip_address = Column(String(64), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_audit_logs_actor", actor_user_id),
        Index("idx_audit_logs_action", action),
        Index("idx_audit_logs_resource", resource_type, resource_id),
        Index("idx_audit_logs_created_at", created_at),
        CheckConstraint("status IN ('succeeded', 'failed')", name="chk_audit_logs_status"),
    )
