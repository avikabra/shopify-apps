"""Simple Returns & Exchanges — FastAPI application.

Run locally:
    uvicorn app.main:app --reload

Run in production:
    uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager

from fastapi import Depends, HTTPException, Request
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .config import get_settings
from .db import Base, engine
from .deps import AuthBounce, get_db
from .routes import admin, auth, billing, portal, webhooks

_MYSHOPIFY_ORIGIN = re.compile(r"^https://[a-z0-9][a-z0-9-]*\.myshopify\.com$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # For the MVP we create tables at startup. Production deployments with
    # real migrations should run `alembic upgrade head` instead and can set
    # SKIP_AUTO_CREATE_TABLES=1.
    import os

    if os.environ.get("SKIP_AUTO_CREATE_TABLES") != "1":
        Base.metadata.create_all(bind=engine)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Simple Returns & Exchanges", lifespan=lifespan)

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

    @app.middleware("http")
    async def portal_cors(request: Request, call_next):
        """Minimal CORS for the customer portal API.

        Preflight (OPTIONS) is answered for *.myshopify.com origins; the
        actual POST handlers enforce the shop-specific origin check.
        """
        if request.url.path.startswith("/portal"):
            origin = request.headers.get("origin", "")
            allowed = settings.use_mock_shopify or bool(_MYSHOPIFY_ORIGIN.match(origin))
            if request.method == "OPTIONS":
                from fastapi.responses import Response

                resp = Response(status_code=204)
                if allowed and origin:
                    resp.headers["Access-Control-Allow-Origin"] = origin
                    resp.headers["Vary"] = "Origin"
                resp.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
                resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
                resp.headers["Access-Control-Max-Age"] = "600"
                return resp
            response = await call_next(request)
            if allowed and origin:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Vary"] = "Origin"
            return response
        return await call_next(request)

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

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

    @app.get("/privacy", response_class=HTMLResponse)
    async def privacy():
        # Public page for the Shopify App Store listing. No auth required.
        from .privacy import PRIVACY_HTML

        return PRIVACY_HTML

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
            from .models import Plan, ReturnRule, Shop

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
                db.flush()
                db.add(
                    ReturnRule(
                        shop_id=existing.id,
                        name="Default 30-day window",
                        enabled=True,
                        priority=0,
                        window_days=30,
                        auto_approve_reasons=[],
                        excluded_product_ids=[],
                        excluded_product_tags=["final-sale"],
                        max_quantity=10,
                    )
                )
                db.commit()
            return RedirectResponse(f"/app?shop={shop_domain}", status_code=302)

    app.include_router(auth.router)
    app.include_router(webhooks.router)
    app.include_router(admin.router)
    app.include_router(billing.router)
    app.include_router(portal.router)

    return app


app = create_app()
