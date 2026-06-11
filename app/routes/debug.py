"""Development debug routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api_support.helpers import _memory_compression_skip_reason, _memory_service
from app.auth.dependencies import get_current_user
from app.auth.repository import get_chat_session, get_db
from app.storage.auth_models import User


router = APIRouter()


@router.get("/debug/memory/{session_id}")
async def debug_memory(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Development-only memory snapshot for validating summary compression."""

    session = get_chat_session(db, session_id)
    if not session or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")

    return _memory_service().debug_snapshot(session_id, db=db)


@router.post("/debug/memory/{session_id}/compress")
async def debug_compress_memory(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Development-only endpoint to manually trigger session summary compression."""

    session = get_chat_session(db, session_id)
    if not session or session.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="会话不存在")

    memory_service = _memory_service()
    before = memory_service.debug_snapshot(session_id, db=db)
    plan_before = memory_service.compression_plan(session_id, db=db)
    memory = memory_service.load(session_id)
    try:
        compressed = memory_service.maybe_compress(
            session_id,
            memory,
            db=db,
            owner_user_id=current_user.id,
        )
        if compressed:
            memory_service.save(session_id, memory)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"memory compression failed: {exc}") from exc

    after = memory_service.debug_snapshot(session_id, db=db)
    plan_after = memory_service.compression_plan(session_id, db=db)
    return {
        "session_id": session_id,
        "compressed": compressed,
        "skip_reason": _memory_compression_skip_reason(compressed, plan_before, after),
        "plan_before": plan_before,
        "plan_after": plan_after,
        "before": {
            "postgres_message_count": before["postgres"]["message_count"],
            "summary_exists": before["summary"]["exists"],
            "redis_memory_message_count": before["redis"]["memory_message_count"],
        },
        "after": {
            "postgres_message_count": after["postgres"]["message_count"],
            "summary_exists": after["summary"]["exists"],
            "covered_until_message_id": after["summary"]["covered_until_message_id"],
            "covered_message_count": after["summary"]["covered_message_count"],
            "redis_memory_message_count": after["redis"]["memory_message_count"],
            "used_summary": after["prompt_context"]["used_summary"],
        },
        "summary": after["summary"],
    }
