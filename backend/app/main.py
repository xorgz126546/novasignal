from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import scheduler
from .api import admin, auth, dashboard, public, settings, signals, telegram, validation
from .config import get_settings
from .db import SessionLocal, init_db
from .security import limiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cryptonex")


def _bootstrap_admin() -> None:
    from sqlalchemy import select

    from .models import Role, Subscription, User
    from .security import hash_password

    s = get_settings()
    if not (s.bootstrap_admin_email and s.bootstrap_admin_password):
        return
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.role == Role.ADMIN.value)):
            return
        u = User(email=s.bootstrap_admin_email.lower(), password_hash=hash_password(s.bootstrap_admin_password),
                 role=Role.ADMIN.value, display_name="Owner")
        db.add(u)
        db.flush()
        db.add(Subscription(user_id=u.id, status="none"))
        db.commit()
        log.info("bootstrap admin created")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _bootstrap_admin()
    if get_settings().enable_scheduler:
        scheduler.start()
    yield
    await scheduler.stop()
    from .market.binance import _client

    if _client:
        await _client.close()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="CRYPTONEX AI API", version="1.0.0", lifespan=lifespan,
                  docs_url="/api/docs" if not s.is_production else None, openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        ip = request.client.host if request.client else "unknown"
        if request.url.path.startswith("/api/") and not limiter.check(f"global:{ip}", 600, 60):
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        if s.is_production:
            resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return resp

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    for r in (public.router, auth.router, signals.router, dashboard.router, settings.router, validation.router,
              admin.router, telegram.router):
        app.include_router(r)
    return app


app = create_app()
