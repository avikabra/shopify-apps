"""Merchant admin (embedded app) routes.

Auth: App Bridge session token (production) or ?shop= in mock/dev mode.
All mutating actions are POSTs and redirect back to the detail page.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..deps import (
    email_provider,
    get_current_shop,
    get_db,
    get_settings_dep,
    shipping_provider,
    shopify_client,
)
from ..models import PLAN_LIMITS, Plan, ReturnRequest, ReturnRule, ReturnStatus, Shop
from ..services import ReturnService

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _service(db: Session, settings: Settings) -> ReturnService:
    return ReturnService(
        db=db,
        shopify=shopify_client(),
        shipping=shipping_provider(),
        email=email_provider(),
        app_url=settings.app_url,
    )


def _ctx(request: Request, shop: Shop, settings: Settings, **kwargs) -> dict:
    return {
        "request": request,
        "shop": shop,
        "settings": settings,
        "api_key": settings.shopify_api_key,
        **kwargs,
    }


@router.get("/app", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    status: str = "",
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    q = db.query(ReturnRequest).filter_by(shop_id=shop.id).order_by(ReturnRequest.id.desc())
    if status:
        try:
            q = q.filter_by(status=ReturnStatus(status))
        except ValueError:
            pass
    requests = q.limit(100).all()
    counts = {
        s.value: db.query(ReturnRequest).filter_by(shop_id=shop.id, status=s).count()
        for s in ReturnStatus
    }
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        _ctx(request, shop, settings, requests=requests, counts=counts,
             active_status=status, ReturnStatus=ReturnStatus),
    )


@router.get("/app/requests/{request_id}", response_class=HTMLResponse)
async def request_detail(
    request_id: int,
    request: Request,
    msg: str = "",
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    req = _get_request(db, shop, request_id)
    svc = _service(db, settings)
    return templates.TemplateResponse(
        request,
        "request_detail.html",
        _ctx(request, shop, settings, req=req, msg=msg,
             refund_preview=svc.refund_preview(req)),
    )


def _get_request(db: Session, shop: Shop, request_id: int) -> ReturnRequest:
    req = (
        db.query(ReturnRequest)
        .filter_by(shop_id=shop.id, id=request_id)
        .one_or_none()
    )
    if not req:
        raise HTTPException(404, "Return request not found")
    return req


def _back(request_id: int, msg: str, shop_param: str) -> RedirectResponse:
    url = f"/app/requests/{request_id}?msg={msg}"
    if shop_param:
        url += f"&shop={shop_param}"
    return RedirectResponse(url, status_code=303)


@router.post("/app/requests/{request_id}/approve")
async def approve_request(
    request_id: int,
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    req = _get_request(db, shop, request_id)
    await _service(db, settings).approve(req, decided_by="merchant")
    return _back(request_id, "approved", request.query_params.get("shop", ""))


@router.post("/app/requests/{request_id}/reject")
async def reject_request(
    request_id: int,
    request: Request,
    note: str = Form(""),
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    req = _get_request(db, shop, request_id)
    await _service(db, settings).reject(req, note, decided_by="merchant")
    return _back(request_id, "rejected", request.query_params.get("shop", ""))


@router.post("/app/requests/{request_id}/settle")
async def settle_request(
    request_id: int,
    request: Request,
    restock: str = Form("yes"),
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    req = _get_request(db, shop, request_id)
    try:
        await _service(db, settings).settle(req, restock=(restock == "yes"))
        msg = "settled"
    except Exception as exc:  # noqa: BLE001 - surface to merchant
        msg = f"error: {exc}"
    return _back(request_id, msg, request.query_params.get("shop", ""))


@router.post("/app/requests/{request_id}/label")
async def retry_label(
    request_id: int,
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    req = _get_request(db, shop, request_id)
    svc = _service(db, settings)
    try:
        await svc.generate_label(req)
        await svc.send_approval_email(req)
        msg = "label-created"
    except Exception as exc:  # noqa: BLE001
        msg = f"error: {exc}"
    return _back(request_id, msg, request.query_params.get("shop", ""))


@router.get("/app/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    msg: str = "",
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    rules = db.query(ReturnRule).filter_by(shop_id=shop.id).order_by(ReturnRule.priority).all()
    return templates.TemplateResponse(
        request,
        "settings.html",
        _ctx(request, shop, settings, rules=rules, msg=msg,
             shop_settings=shop.settings or {}),
    )


@router.post("/app/settings/general")
async def settings_general(
    request: Request,
    brand_name: str = Form(""),
    support_email: str = Form(""),
    custom_domains: str = Form(""),
    wh_street1: str = Form(""), wh_street2: str = Form(""),
    wh_city: str = Form(""), wh_state: str = Form(""),
    wh_zip: str = Form(""), wh_country: str = Form("US"),
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
):
    s = dict(shop.settings or {})
    s["brand_name"] = brand_name.strip()
    s["support_email"] = support_email.strip()
    s["custom_domains"] = custom_domains.strip()
    s["warehouse_address"] = {
        "street1": wh_street1.strip(), "street2": wh_street2.strip(),
        "city": wh_city.strip(), "state": wh_state.strip(),
        "zip": wh_zip.strip(), "country": wh_country.strip() or "US",
    }
    shop.settings = s
    db.commit()
    url = "/app/settings?msg=saved"
    if request.query_params.get("shop"):
        url += f"&shop={request.query_params['shop']}"
    return RedirectResponse(url, status_code=303)


@router.post("/app/settings/rules")
async def settings_rule(
    request: Request,
    name: str = Form(""),
    window_days: int = Form(30),
    reasons: str = Form(""),
    excluded_tags: str = Form(""),
    max_quantity: int = Form(10),
    enabled: str = Form("yes"),
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
):
    existing = db.query(ReturnRule).filter_by(shop_id=shop.id).count()
    db.add(
        ReturnRule(
            shop_id=shop.id,
            name=name.strip() or f"Rule {existing + 1}",
            enabled=(enabled == "yes"),
            priority=existing,
            window_days=max(1, min(window_days, 365)),
            auto_approve_reasons=[r.strip().upper() for r in reasons.split(",") if r.strip()],
            excluded_product_ids=[],
            excluded_product_tags=[t.strip().lower() for t in excluded_tags.split(",") if t.strip()],
            max_quantity=max(1, min(max_quantity, 999)),
        )
    )
    db.commit()
    url = "/app/settings?msg=rule-saved"
    if request.query_params.get("shop"):
        url += f"&shop={request.query_params['shop']}"
    return RedirectResponse(url, status_code=303)


@router.post("/app/settings/rules/{rule_id}/delete")
async def settings_rule_delete(
    rule_id: int,
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
):
    rule = db.query(ReturnRule).filter_by(shop_id=shop.id, id=rule_id).one_or_none()
    if rule:
        db.delete(rule)
        db.commit()
    url = "/app/settings?msg=rule-deleted"
    if request.query_params.get("shop"):
        url += f"&shop={request.query_params['shop']}"
    return RedirectResponse(url, status_code=303)


@router.get("/app/billing", response_class=HTMLResponse)
async def billing_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    from datetime import datetime, timezone

    from ..models import PlanUsage

    month = f"{datetime.now(timezone.utc).year:04d}-{datetime.now(timezone.utc).month:02d}"
    usage = db.query(PlanUsage).filter_by(shop_id=shop.id, month=month).one_or_none()
    used = usage.returns_count if usage else 0
    plans = [
        {
            "id": plan.value,
            "name": plan.value.title(),
            "price_cents": info["price_cents"],
            "monthly_returns": info["monthly_returns"],
            "current": shop.plan == plan,
        }
        for plan, info in PLAN_LIMITS.items()
    ]
    limit = PLAN_LIMITS[shop.plan]["monthly_returns"]
    return templates.TemplateResponse(
        request,
        "billing.html",
        _ctx(request, shop, settings, plans=plans, used=used, month=month, limit=limit),
    )


@router.post("/app/billing/plan")
async def billing_change_plan(
    request: Request,
    plan: str = Form("starter"),
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
):
    # NOTE: v1 changes the plan record directly (demo/dev). Before app-store
    # submission this must be replaced with Shopify Billing API subscriptions.
    try:
        shop.plan = Plan(plan)
    except ValueError:
        raise HTTPException(400, "Unknown plan")
    db.commit()
    url = "/app/billing"
    if request.query_params.get("shop"):
        url += f"?shop={request.query_params['shop']}"
    return RedirectResponse(url, status_code=303)
