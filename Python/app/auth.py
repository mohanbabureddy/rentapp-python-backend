import logging
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Callable, Optional

import jwt
from dotenv import load_dotenv
from flask import g, jsonify, request

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET not set. Define it in .env before starting the app.")

JWT_ALGORITHM = "HS256"
TOKEN_TTL = timedelta(hours=12)

logger = logging.getLogger("app.auth")


def generate_token(username: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "role": role, "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


def _extract_token() -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip()
    return None


def _authenticate() -> Optional[dict]:
    """Validates the bearer token and, on success, stores {"username", "role"} on
    flask.g.current_user for the rest of the request. Returns None on failure."""
    token = _extract_token()
    payload = _decode_token(token) if token else None
    if payload is None:
        return None
    g.current_user = {"username": payload["sub"], "role": payload.get("role")}
    return g.current_user


def require_auth(fn: Callable) -> Callable:
    """Any authenticated user, regardless of role."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if _authenticate() is None:
            return jsonify({"error": "Authentication required"}), 401
        return fn(*args, **kwargs)
    return wrapper


def require_role(*roles: str) -> Callable:
    """Only an authenticated user whose role is in `roles`."""
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = _authenticate()
            if user is None:
                return jsonify({"error": "Authentication required"}), 401
            if user["role"] not in roles:
                logger.warning("Forbidden: user '%s' (role=%s) attempted %s.", user["username"], user["role"], request.path)
                return jsonify({"error": "Forbidden"}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def require_self_or_admin(get_target_username: Callable[..., Optional[str]]) -> Callable:
    """Allow ADMIN unconditionally; anyone else only if their own username matches
    whatever `get_target_username(*args, **kwargs)` returns (a path param, JSON body
    field, etc. -- the route decides how to find the tenant this request is about)."""
    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = _authenticate()
            if user is None:
                return jsonify({"error": "Authentication required"}), 401
            if user["role"] != "ADMIN":
                target = get_target_username(*args, **kwargs)
                if target != user["username"]:
                    logger.warning("Forbidden: user '%s' attempted to access data for tenant '%s' at %s.",
                                   user["username"], target, request.path)
                    return jsonify({"error": "Forbidden"}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator
