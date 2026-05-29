# app/auth/security.py
"""Password hashing and JWT utilities."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from typing import Optional

import jwt

from app.config import settings


# ============================================================
# Password Hashing (PBKDF2-SHA256)
# ============================================================

def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-SHA256.

    Returns a string in the format: pbkdf2_sha256$iterations$salt$hash
    """
    salt = os.urandom(16).hex()
    iterations = settings.password_hash_iterations
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a stored hash.

    Supports format: pbkdf2_sha256$iterations$salt$hash
    """
    try:
        parts = password_hash.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False

        _, iterations_str, salt, stored_hash = parts
        iterations = int(iterations_str)

        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        )
        return hmac.compare_digest(dk.hex(), stored_hash)
    except Exception:
        return False


# ============================================================
# JWT Token
# ============================================================

def create_access_token(
    user_id: str,
    role: str,
    expires_delta: Optional[int] = None,
) -> str:
    """Create a JWT access token.

    Args:
        user_id: The user's ID (stored in 'sub' and 'user_id' claims)
        role: The user's role ('admin' or 'user')
        expires_delta: Token lifetime in minutes (default from settings)

    Returns:
        Encoded JWT string
    """
    now = int(time.time())
    expire_minutes = expires_delta or settings.jwt_access_token_expire_minutes
    exp = now + (expire_minutes * 60)

    payload = {
        "sub": user_id,
        "user_id": user_id,
        "role": role,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": exp,
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT access token.

    Returns:
        The token payload dict if valid, None otherwise
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
