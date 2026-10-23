"""Billing routes (Shopify Billing API).

Mock/dev mode: plan changes are recorded directly in the DB (no network).
Production: Pro goes through Shopify's appSubscriptionCreate flow —
the merchant approves on Shopify, then returns to /app/billing/callback.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import billing as billing_svc
from .. import security, tokens
from ..config import Settings, get_settings
from ..deps import get_current_shop, get_db, get_settings_dep, shopify_client
from ..models import (
    PLAN_LIMITS,
    AgentPromotion,
    Plan,
    PricingRule,
    Shop,
    VisibilityRule,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

RULE_KINDS = [
    ("visibility_rules", "visibility rules", VisibilityRule),
    ("pricing_rules", "pricing rules", PricingRule),
    ("promotions", "agent promotions", AgentPromotion),
]


def _ctx(request: Request, shop: Shop, settings: Settings, **kwargs) -> dict:
    return {
        "request": request,
        "shop": shop,
        "settings": settings,
        "api_key": settings.shopify_api_key,
        **kwargs,
    }


def _token(shop: Shop, settings: Settings) -> str:
    try:
        return tokens.fresh_access_token(shop, settings)
    except RuntimeError:
        return "mock"


def _usage(db: Session, shop: Shop) -> dict[str, dict]:
    """Current rule counts and plan limits for the billing page."""
    out = {}
    for kind, label, model in RULE_KINDS:
        count = db.query(model).filter_by(shop_id=shop.id).count()
        limit = PLAN_LIMITS[shop.plan][kind]
        out[kind] = {"label": label, "used": count, "limit": limit}
    return out


def _back_url(request: Request, path: str) -> str:
    url = path
    shop_param = request.query_params.get("shop")
    if shop_param:
        url += f"?shop={shop_param}"
    return url


@router.get("/app/billing", response_class=HTMLResponse)
async def billing_page(
    request: Request,
    msg: str = "",
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    plans = [
        {
            "id": plan.value,
            "name": plan.value.title(),
            "price_cents": PLAN_LIMITS[plan]["price_cents"],
            "trial_days": billing_svc.TRIAL_DAYS[plan],
            "features": billing_svc.PLAN_FEATURES[plan],
            "current": shop.plan == plan,
        }
        for plan in billing_svc.PLAN_ORDER
    ]
    return templates.TemplateResponse(
        request,
        "billing.html",
        _ctx(
            request,
            shop,
            settings,
            plans=plans,
            usage=_usage(db, shop),
            msg=msg,
            mock_mode=settings.use_mock_shopify,
            subscription_status=shop.subscription_status,
            trial_ends_at=shop.trial_ends_at,
        ),
    )


def _confirmation_top_redirect(confirmation_url: str) -> HTMLResponse:
    """Navigate the TOP frame to Shopify's charge confirmation page.

    The subscribe form posts inside the embedded iframe. Answering with a
    303 straight to the confirmation URL loads admin.shopify.com inside
    the iframe, which refuses framing — the merchant (and Shopify's
    reviewer, 2026-10-19) sees "refused to connect" and billing can never
    be verified. This page breaks out to the top level instead, the same
    pattern as the auth bounce.
    """
    import html as _html
    import json as _json

    url_html = _html.escape(confirmation_url, quote=True)
    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Confirm your subscription</title></head><body>"
        "<p style='font-family:system-ui,sans-serif'>Taking you to Shopify "
        f'to confirm your subscription&hellip; <a href="{url_html}" '
        'target="_top">Continue</a></p>'
        "<script>"
        f"var target={_json.dumps(confirmation_url)};"
        "function go(){try{window.top.location.href=target;}"
        "catch(e){window.location.href=target;}}"
        "go();setTimeout(go,1500);"
        "</script></body></html>"
    )
    return HTMLResponse(page, status_code=200)


@router.post("/app/billing/subscribe")
async def billing_subscribe(
    request: Request,
    plan: str = Form("free"),
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    try:
        target = Plan(plan)
    except ValueError:
        raise HTTPException(400, "Unknown plan")

    if target == shop.plan:
        return RedirectResponse(_back_url(request, "/app/billing"), status_code=303)

    client = shopify_client()

    # Free plan: cancel any Shopify subscription, record locally.
    if target == Plan.FREE:
        if not settings.use_mock_shopify and shop.app_subscription_id:
            try:
                await client.cancel_app_subscription(
                    _token(shop, settings), shop.shop_domain, shop.app_subscription_id
                )
            except Exception as exc:  # noqa: BLE001 - best effort
                raise HTTPException(502, f"Could not cancel subscription: {exc}")
        billing_svc.downgrade_to_free(db, shop)
        return RedirectResponse(
            _back_url(request, "/app/billing?msg=downgraded"), status_code=303
        )

    # Mock/dev: record the plan directly (no Shopify round-trip).
    if settings.use_mock_shopify:
        shop.plan = target
        shop.app_subscription_id = None
        shop.subscription_status = "ACTIVE"
        db.commit()
        return RedirectResponse(
            _back_url(request, "/app/billing?msg=plan-updated"), status_code=303
        )

    # Production: create the subscription and send the merchant to Shopify.
    if shop.app_subscription_id:
        try:
            await client.cancel_app_subscription(
                _token(shop, settings), shop.shop_domain, shop.app_subscription_id
            )
        except Exception as exc:  # noqa: BLE001 - replacing anyway
            raise HTTPException(502, f"Could not replace subscription: {exc}")

    return_url = f"{settings.app_url}/app/billing/callback"
    shop_param = request.query_params.get("shop")
    if shop_param:
        return_url += f"?shop={shop_param}"
    try:
        _sub_id, confirmation_url = await client.create_app_subscription(
            _token(shop, settings),
            shop.shop_domain,
            name=billing_svc.subscription_name_for_plan(target),
            price_cents=PLAN_LIMITS[target]["price_cents"],
            currency="USD",
            trial_days=billing_svc.TRIAL_DAYS[target],
            return_url=return_url,
            test=settings.shopify_billing_test,
        )
    except Exception as exc:  # noqa: BLE001 - surface to merchant
        raise HTTPException(502, f"Could not create subscription: {exc}")
    return _confirmation_top_redirect(confirmation_url)


@router.get("/app/billing/callback")
async def billing_callback(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    """Shopify redirects here after the merchant approves/declines."""
    try:
        await billing_svc.sync_shop_billing(
            db, shop, shopify_client(), _token(shop, settings)
        )
    except Exception:  # noqa: BLE001 - don't strand the merchant
        return RedirectResponse(
            _back_url(request, "/app/billing?msg=sync-failed"),
            status_code=303,
        )
    return RedirectResponse(
        _back_url(request, "/app/billing?msg=subscription-active"), status_code=303
    )


@router.post("/app/billing/cancel")
async def billing_cancel(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    """Cancel the current paid subscription and drop to Free."""
    if not settings.use_mock_shopify and shop.app_subscription_id:
        try:
            await shopify_client().cancel_app_subscription(
                _token(shop, settings), shop.shop_domain, shop.app_subscription_id
            )
        except Exception as exc:  # noqa: BLE001 - surface to merchant
            raise HTTPException(502, f"Could not cancel subscription: {exc}")
    billing_svc.downgrade_to_free(db, shop)
    return RedirectResponse(
        _back_url(request, "/app/billing?msg=cancelled"), status_code=303
    )
