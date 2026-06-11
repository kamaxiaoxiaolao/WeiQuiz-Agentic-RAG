"""Chat session routes."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api_support.helpers import _memory_service
from app.api_support.schemas import UpdateSessionTitleRequest
from app.auth.dependencies import get_current_user
from app.auth.repository import (
    create_chat_session,
    get_chat_session,
    get_db,
    list_chat_messages,
    list_user_sessions,
    soft_delete_session,
)
from app.storage.auth_models import User


router = APIRouter()


@router.get("/sessions")
async def list_sessions(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前用户的会话列表"""
    sessions = list_user_sessions(db, current_user.id, limit)
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "title": s.title,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
            }
            for s in sessions
        ]
    }


@router.post("/sessions")
async def create_session(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建新会话"""
    session_id = uuid.uuid4().hex
    title = "新会话"
    session = create_chat_session(db, session_id, current_user.id, title)
    return {
        "session_id": session.session_id,
        "title": session.title,
        "created_at": session.created_at.isoformat() if session.created_at else None,
    }


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除会话（需验证归属）"""
    session = get_chat_session(db, session_id)
    if not session or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    soft_delete_session(db, session)
    _memory_service().delete(session_id)
    return {"ok": True}


@router.put("/sessions/{session_id}/title")
async def update_session_title(
    session_id: str,
    request: UpdateSessionTitleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新会话标题（需验证归属）"""
    session = get_chat_session(db, session_id)
    if not session or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    title = request.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    
    session.title = title
    session.updated_at = datetime.utcnow()
    db.commit()
    
    return {"ok": True, "title": session.title}


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取会话消息（需验证归属）"""
    session = get_chat_session(db, session_id)
    if not session or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    persisted_messages = list_chat_messages(db, session_id)
    if persisted_messages:
        messages = []
        for message in persisted_messages:
            row = {"role": message.role, "content": message.content}
            metadata = message.metadata_json or {}
            if message.role == "assistant":
                row["sources"] = metadata.get("sources", [])
                row["citations"] = metadata.get("citations", [])
                row["route"] = metadata.get("route")
                row["trace"] = metadata.get("trace")
            messages.append(row)
        return {"session_id": session_id, "messages": messages}

    return {"session_id": session_id, "messages": _memory_service().messages(session_id)}
