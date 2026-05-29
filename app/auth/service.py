# app/auth/service.py
"""Auth business logic: register, login, bootstrap admin."""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.auth.repository import (
    create_user,
    get_user_by_email,
    get_user_by_username,
    get_user_by_id,
    is_users_table_empty,
    update_last_login,
)
from app.auth.security import create_access_token, hash_password, verify_password
from app.auth.schemas import LoginResponse, UserResponse
from app.config import settings
from app.storage.auth_models import User


def _user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        role=user.role,
        status=user.status,
        created_at=user.created_at,
    )


def register(
    db: Session,
    username: str,
    password: str,
    display_name: Optional[str] = None,
    email: Optional[str] = None,
    admin_invite_code: Optional[str] = None,
) -> UserResponse:
    """Register a new user.

    Normal registration always creates a user account. Admin accounts require
    an explicit server-side invite code instead of relying on database order.
    """
    if get_user_by_username(db, username):
        raise ValueError("USERNAME_EXISTS")

    if email and get_user_by_email(db, email):
        raise ValueError("EMAIL_EXISTS")

    role = "user"
    if admin_invite_code:
        if not settings.admin_invite_code or admin_invite_code != settings.admin_invite_code:
            raise ValueError("INVALID_ADMIN_INVITE_CODE")
        role = "admin"

    user_id = f"usr_{uuid.uuid4().hex[:16]}"
    password_hash = hash_password(password)

    user = create_user(
        db=db,
        user_id=user_id,
        username=username,
        password_hash=password_hash,
        display_name=display_name or username,
        email=email,
        role=role,
    )
    return _user_to_response(user)


def login(db: Session, username: str, password: str) -> LoginResponse:
    """Login with username and password. Raises ValueError on failure."""
    user = get_user_by_username(db, username)
    if not user:
        raise ValueError("INVALID_CREDENTIALS")

    if not verify_password(password, user.password_hash):
        raise ValueError("INVALID_CREDENTIALS")

    if user.status == "disabled":
        raise ValueError("USER_DISABLED")

    update_last_login(db, user)

    access_token = create_access_token(user_id=user.id, role=user.role)
    expires_in = settings.jwt_access_token_expire_minutes * 60

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=expires_in,
        user=_user_to_response(user),
    )


def get_current_user_info(db: Session, user_id: str) -> Optional[UserResponse]:
    """Get user info by user_id. Returns None if not found or disabled."""
    user = get_user_by_id(db, user_id)
    if not user or user.status == "disabled":
        return None
    return _user_to_response(user)


def bootstrap_admin(db: Session) -> Optional[UserResponse]:
    """Create the first admin user from env vars if table is empty.

    Returns the created user, or None if bootstrap is disabled or not applicable.
    """
    if not settings.auth_bootstrap_admin_enabled:
        return None

    if not settings.auth_bootstrap_admin_username or not settings.auth_bootstrap_admin_password:
        return None

    if not is_users_table_empty(db):
        return None

    user_id = f"adm_{uuid.uuid4().hex[:16]}"
    password_hash = hash_password(settings.auth_bootstrap_admin_password)

    user = create_user(
        db=db,
        user_id=user_id,
        username=settings.auth_bootstrap_admin_username,
        password_hash=password_hash,
        display_name="Administrator",
        email=None,
        role="admin",
    )
    return _user_to_response(user)
