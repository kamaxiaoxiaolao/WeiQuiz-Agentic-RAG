# app/auth/repository.py
"""Data access layer for users and chat_sessions."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.storage.auth_models import Base, User, ChatSession, ChatMessage, SessionSummary


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
