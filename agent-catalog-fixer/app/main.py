"""Agent-Ready Catalog Fixer — FastAPI application.

Run locally:
    uvicorn app.main:app --reload

Run in production:
    uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Depends, Request
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from . import privacy
from .config import get_settings
from .db import Base, engine
from .deps import AuthBounce, get_db
from .models import Plan, Shop
from .routes import admin, auth, billing, webhooks


def _ensure_token_columns() -> None:
    """create_all never alters existing tables. Add the expiring-token
    columns to a shops table created before they existed (the app has
    never been installed, so no data migration is involved)."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "shops" not in inspector.get_table_names():
        return
    existing = {c["name"] for c in inspector.get_columns("shops")}
    dialect = engine.dialect.name
    blob = "BYTEA" if dialect == "postgresql" else "BLOB"
    stamp = "TIMESTAMP" if dialect == "postgresql" else "DATETIME"
    with engine.begin() as conn:
        if "refresh_token_enc" not in existing:
            conn.execute(text(f"ALTER TABLE shops ADD COLUMN refresh_token_enc {blob}"))
        if "access_token_expires_at" not in existing:
            conn.execute(
                text(f"ALTER TABLE shops ADD COLUMN access_token_expires_at {stamp}")
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # For the MVP we create tables at startup. Production deployments with
    # real migrations should run `alembic upgrade head` instead and can set
    # SKIP_AUTO_CREATE_TABLES=1.
    if os.environ.get("SKIP_AUTO_CREATE_TABLES") != "1":
        Base.metadata.create_all(bind=engine)
        _ensure_token_columns()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Agent-Ready Catalog Fixer", lifespan=lifespan)

    @app.exception_handler(AuthBounce)
    async def _auth_bounce(request, exc):  # noqa: ANN001, ANN202
        # Exit-iframe page: restart OAuth at the TOP level. A 302 chain
        # issued inside the admin iframe lands on Shopify's OAuth page,
        # which refuses framing, or loops through the callback.
        from urllib.parse import quote as _q

        auth_url = f"/auth?shop={_q(exc.shop_domain)}"
        if exc.host:
            auth_url += f"&host={_q(exc.host)}"
        page = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Opening app</title></head><body>"
            "<p style='font-family:system-ui,sans-serif'>Opening the app&hellip; "
            f'<a href="{auth_url}" target="_top">Continue</a></p>'
            "<script>"
            f"var target={__import__('json').dumps(auth_url)};"
            "function go(){try{window.top.location.href=target;}"
            "catch(e){window.location.href=target;}}"
            "go();setTimeout(go,1500);"
            "</script></body></html>"
        )
        return HTMLResponse(page, status_code=200)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/media/screencast.mp4")
    async def screencast():
        from pathlib import Path
        from fastapi.responses import FileResponse
        f = Path(__file__).parent / "static" / "screencast.mp4"
        return FileResponse(f, media_type="video/mp4")

    @app.get("/media/proof-fix.png")
    async def proof_fix():
        # Static screenshot used as the "proof of resolution" link for the
        # App Store review items (shows the app rendering/working after
        # the review fixes).
        from pathlib import Path
        from fastapi.responses import FileResponse
        f = Path(__file__).parent / "static" / "proof-fix.png"
        return FileResponse(f, media_type="image/png")

    @app.get("/")
    async def root(request: Request):
        # Shopify loads the App URL in an iframe; redirect to the admin UI,
        # preserving query params (shop, host, etc.).
        qs = str(request.url.query)
        return RedirectResponse(f"/app?{qs}" if qs else "/app", status_code=302)

    # Dev login: mock mode only. Creates a demo shop and drops you in the app.
    if settings.use_mock_shopify:

        @app.get("/dev/login", response_class=HTMLResponse)
        async def dev_login(shop: str = "demo.myshopify.com", db: Session = Depends(get_db)):
            shop_domain = shop.strip().lower()
            existing = db.query(Shop).filter_by(shop_domain=shop_domain).one_or_none()
            if existing is None:
                existing = Shop(
                    shop_domain=shop_domain,
                    access_token_enc=b"mock",
                    plan=Plan(settings.resolved_default_plan),
                    settings={"brand_name": "Demo Store"},
                )
                db.add(existing)
                db.commit()
            return RedirectResponse(f"/app?shop={shop_domain}", status_code=302)

    app.include_router(auth.router)
    app.include_router(webhooks.router)
    app.include_router(admin.router)
    app.include_router(billing.router)
    app.include_router(privacy.router)

    return app


app = create_app()
