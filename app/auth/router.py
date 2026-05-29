"""Auth API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.repository import get_db
from app.auth.schemas import (
    ErrorResponse,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    UserResponse,
)
from app.auth.service import get_current_user_info, login, register
from app.storage.auth_models import User

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": ErrorResponse, "description": "用户名或邮箱已存在"},
        422: {"model": ErrorResponse, "description": "参数校验失败"},
    },
)
async def register_user(
    request: RegisterRequest,
    db: Session = Depends(get_db),
):
    """注册新用户。

    - 用户名必须唯一
    - 邮箱可选，提供时必须唯一
    - 默认角色为 user
    """
    try:
        user = register(
            db=db,
            username=request.username,
            password=request.password,
            display_name=request.display_name,
            email=request.email,
            admin_invite_code=request.admin_invite_code,
        )
        return user
    except ValueError as e:
        error_code = str(e)
        if error_code == "USERNAME_EXISTS":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"code": "AUTH_USERNAME_EXISTS", "message": "用户名已存在"}},
            )
        elif error_code == "EMAIL_EXISTS":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": {"code": "AUTH_EMAIL_EXISTS", "message": "邮箱已被注册"}},
            )
        elif error_code == "INVALID_ADMIN_INVITE_CODE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "AUTH_INVALID_ADMIN_INVITE_CODE", "message": "管理员邀请码无效"}},
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": {"code": "AUTH_REGISTER_FAILED", "message": "注册失败"}},
        )


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={
        401: {"model": ErrorResponse, "description": "用户名或密码错误"},
    },
)
async def login_user(
    request: LoginRequest,
    db: Session = Depends(get_db),
):
    """用户登录，返回 JWT access token。"""
    try:
        response = login(
            db=db,
            username=request.username,
            password=request.password,
        )
        return response
    except ValueError as e:
        error_code = str(e)
        if error_code in ("INVALID_CREDENTIALS", "USER_DISABLED"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": "AUTH_INVALID_CREDENTIALS", "message": "用户名或密码错误"}},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "AUTH_LOGIN_FAILED", "message": "登录失败"}},
        )


@router.get(
    "/me",
    response_model=UserResponse,
    responses={
        401: {"model": ErrorResponse, "description": "未登录或 token 无效"},
    },
)
async def get_me(
    current_user: User = Depends(get_current_user),
):
    """获取当前登录用户信息。"""
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        display_name=current_user.display_name,
        email=current_user.email,
        role=current_user.role,
        status=current_user.status,
        created_at=current_user.created_at,
    )
