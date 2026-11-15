"""Authentication, authorization, rate limiting and audit helpers."""
from __future__ import annotations

import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AuditLog, Role, Subscription, User

ALGO = "HS256"
_bearer = HTTPBearer(auto_error=False)


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user.id), "role": user.role, "iat": now, "exp": now + timedelta(minutes=s.access_token_minutes)}
    return jwt.encode(payload, s.secret_key, algorithm=ALGO)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, get_settings().secret_key, algorithms=[ALGO])
        uid = int(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    user = db.get(User, uid)
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account disabled or missing")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != Role.ADMIN.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return user


def has_active_access(user: User, now: datetime | None = None) -> bool:
    """Access control for paid features. While billing is disabled (private testing) all active users have access."""
    if user.role == Role.ADMIN.value or not get_settings().billing_enabled:
        return True
    sub: Subscription | None = user.subscription
    now = now or datetime.now(timezone.utc)
    return bool(
        sub and sub.status in ("active", "trial") and sub.current_period_end and sub.current_period_end > now
    )


def require_subscriber(user: User = Depends(get_current_user)) -> User:
    if not has_active_access(user):
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, "Active subscription required")
    return user


def new_link_code() -> str:
    return secrets.token_urlsafe(9)


def audit(db: Session, actor: str, action: str, **details) -> None:
    db.add(AuditLog(actor=actor, action=action, details=details))
    db.commit()


class RateLimiter:
    """In-process sliding-window limiter. For multi-instance deployments put a shared limiter (e.g. at the proxy/Redis) in front."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_s: float) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limit(limit: int, window_s: float, scope: str):
    def dep(request: Request):
        ip = request.client.host if request.client else "unknown"
        if not limiter.check(f"{scope}:{ip}", limit, window_s):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded, slow down")

    return dep
