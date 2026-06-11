"""Request models shared by API route modules."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class QueryRequest(BaseModel):
    question: str


class ChatRequest(BaseModel):
    session_id: str
    message: str
    grounding_mode: str = "off"


class AdminUserUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None


class AdminUserCreateRequest(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    role: str = "user"


class UpdateSessionTitleRequest(BaseModel):
    title: str
