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
