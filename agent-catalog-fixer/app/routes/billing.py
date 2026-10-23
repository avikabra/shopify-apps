"""Billing routes (Shopify Billing API).

Mock/dev mode: plan changes are recorded directly in the DB (no network).
Production: paid plans go through Shopify's appSubscriptionCreate flow —
the merchant approves on Shopify, then returns to /app/billing/callback.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session
from pathlib import Path

from .. import billing as billing_svc
from ..config import Settings, get_settings
from ..deps import get_current_shop, get_db, get_settings_dep, shopify_client_for
from ..models import Plan, Shop

router = APIRouter()

_templates_dir = Path(__file__).parent.parent / "templates"
_jinja = Environment(
    loader=FileSystemLoader(str(_templates_dir)),
    autoescape=select_autoescape(["html"]),
)
_jinja.globals["shopify_api_key"] = get_settings().shopify_api_key


def _back_url(request: Request, path: str) -> str:
    url = path
    shop_param = request.query_params.get("shop")
    if shop_param:
        sep = "&" if "?" in url else "?"
        url += f"{sep}shop={shop_param}"
    return url


def _plan_cards(shop: Shop, settings: Settings) -> list[dict]:
    return [
        {
            "id": plan.value,
            "name": plan.value.title(),
            "price_cents": billing_svc.PLAN_PRICE_CENTS[plan],
            "trial_days": billing_svc.TRIAL_DAYS[plan],
            "features": billing_svc.PLAN_FEATURES[plan],
            "current": shop.plan == plan,
        }
        for plan in billing_svc.PLAN_ORDER
    ]


@router.get("/app/billing", response_class=HTMLResponse)
def billing_page(
    request: Request,
    response: Response,
    msg: str = "",
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    html = _jinja.get_template("billing.html").render(
        request=request,
        shop=shop,
        plan=shop.plan.value,
        plans=_plan_cards(shop, settings),
        msg=msg,
        mock_mode=settings.use_mock_shopify,
        subscription_status=shop.subscription_status,
        trial_ends_at=shop.trial_ends_at,
    )
    return HTMLResponse(html)


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
def billing_subscribe(
    request: Request,
    response: Response,
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

    # Free plan: cancel any Shopify subscription, record locally.
    if target == Plan.FREE:
        if not settings.use_mock_shopify and shop.app_subscription_id:
            try:
                shopify_client_for(shop).cancel_app_subscription(shop.app_subscription_id)
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
            shopify_client_for(shop).cancel_app_subscription(shop.app_subscription_id)
        except Exception as exc:  # noqa: BLE001 - replacing anyway
            raise HTTPException(502, f"Could not replace subscription: {exc}")

    return_url = f"{settings.app_url}/app/billing/callback"
    shop_param = request.query_params.get("shop")
    if shop_param:
        return_url += f"?shop={shop_param}"
    try:
        _sub_id, confirmation_url = shopify_client_for(shop).create_app_subscription(
            name=billing_svc.subscription_name_for_plan(target),
            price_cents=billing_svc.PLAN_PRICE_CENTS[target],
            currency="USD",
            trial_days=billing_svc.TRIAL_DAYS[target],
            return_url=return_url,
            test=settings.shopify_billing_test,
        )
    except Exception as exc:  # noqa: BLE001 - surface to merchant
        raise HTTPException(502, f"Could not create subscription: {exc}")
    return _confirmation_top_redirect(confirmation_url)


@router.get("/app/billing/callback")
def billing_callback(
    request: Request,
    response: Response,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    """Shopify redirects here after the merchant approves/declines."""
    try:
        plan = billing_svc.sync_shop_billing(db, shop, shopify_client_for(shop))
    except Exception:  # noqa: BLE001 - don't strand the merchant
        return RedirectResponse(
            _back_url(request, "/app/billing?msg=sync-failed"),
            status_code=303,
        )
    _ = plan
    return RedirectResponse(
        _back_url(request, "/app/billing?msg=subscription-active"), status_code=303
    )


@router.post("/app/billing/cancel")
def billing_cancel(
    request: Request,
    response: Response,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    """Cancel the current paid subscription and drop to Free."""
    if not settings.use_mock_shopify and shop.app_subscription_id:
        try:
            shopify_client_for(shop).cancel_app_subscription(shop.app_subscription_id)
        except Exception as exc:  # noqa: BLE001 - surface to merchant
            raise HTTPException(502, f"Could not cancel subscription: {exc}")
    billing_svc.downgrade_to_free(db, shop)
    return RedirectResponse(
        _back_url(request, "/app/billing?msg=cancelled"), status_code=303
    )
