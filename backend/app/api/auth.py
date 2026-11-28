from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Role, Subscription, User, utcnow
from ..security import (audit, create_token, get_current_user, has_active_access, hash_password, rate_limit,
                        verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field("", max_length=120)
    accept_risk_disclosure: bool


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    role: str
    created_at: datetime
    telegram_linked: bool
    has_access: bool
    subscription_status: str
    notification_prefs: dict


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, display_name=u.display_name, role=u.role, created_at=u.created_at,
                   telegram_linked=bool(u.telegram_chat_id), has_access=has_active_access(u),
                   subscription_status=u.subscription.status if u.subscription else "none",
                   notification_prefs=u.notification_prefs or {})


@router.post("/register", dependencies=[Depends(rate_limit(5, 3600, "register"))])
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if not body.accept_risk_disclosure:
        raise HTTPException(400, "You must accept the risk disclosure")
    email = body.email.lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(409, "Email already registered")
    if not any(c.isdigit() for c in body.password) or not any(c.isalpha() for c in body.password):
        raise HTTPException(400, "Password must contain letters and digits")
    first = (db.scalar(select(func.count()).select_from(User)) or 0) == 0
    u = User(email=email, password_hash=hash_password(body.password), display_name=body.display_name,
             role=Role.ADMIN.value if first else Role.USER.value)  # first account becomes the owner/admin
    db.add(u)
    db.flush()
    db.add(Subscription(user_id=u.id, status="none"))
    db.commit()
    audit(db, f"user:{u.id}", "register", email=email, role=u.role)
    return {"token": create_token(u), "user": user_out(u)}


@router.post("/login", dependencies=[Depends(rate_limit(10, 300, "login"))])
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if not u or not verify_password(body.password, u.password_hash):
        audit(db, "anonymous", "login_failed", email=body.email.lower())
        raise HTTPException(401, "Invalid email or password")
    if not u.is_active:
        raise HTTPException(403, "Account disabled")
    u.last_login_at = utcnow()
    db.commit()
    audit(db, f"user:{u.id}", "login")
    return {"token": create_token(u), "user": user_out(u)}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_out(user)
