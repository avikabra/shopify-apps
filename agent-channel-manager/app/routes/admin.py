"""Merchant admin (embedded app) routes.

Auth: App Bridge session token (production) or ?shop= in mock/dev mode.
All mutating actions are POSTs and redirect back to their list page.

Plan enforcement: free plan caps visibility_rules (10), pricing_rules (2),
promotions (1). Pro is unlimited. Caps are checked before inserts.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import security, services, tokens
from ..config import Settings, get_settings
from ..deps import (
    check_plan_limit,
    get_current_shop,
    get_db,
    get_settings_dep,
    plan_limit,
    shopify_client,
)
from ..models import (
    PLAN_LIMITS,
    AgentPromotion,
    ChannelMode,
    Plan,
    PricingRule,
    PricingRuleType,
    PricingScope,
    PromotionType,
    Shop,
    VisibilityAction,
    VisibilityRule,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _ctx(request: Request, shop: Shop, settings: Settings, **kwargs) -> dict:
    return {
        "request": request,
        "shop": shop,
        "settings": settings,
        "api_key": settings.shopify_api_key,
        "ChannelMode": ChannelMode,
        "PricingRuleType": PricingRuleType,
        "PromotionType": PromotionType,
        "PLAN_LIMITS": PLAN_LIMITS,
        **kwargs,
    }


def _token(shop: Shop, settings: Settings) -> str:
    try:
        return tokens.fresh_access_token(shop, settings)
    except RuntimeError:
        return "mock"


async def _catalog(shop: Shop, settings: Settings):
    client = shopify_client()
    token = _token(shop, settings)
    products = await client.list_products(token, shop.shop_domain, limit=100)
    collections = await client.list_collections(token, shop.shop_domain, limit=50)
    return products, collections


def _shop_qs(request: Request) -> str:
    shop = request.query_params.get("shop", "")
    return f"?shop={shop}" if shop else ""


# -- dashboard ------------------------------------------------------------

@router.get("/app", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    products, _ = await _catalog(shop, settings)
    views = services.evaluate_channel(products, shop, db)
    summary = services.summarize(views)
    promos = db.query(AgentPromotion).filter_by(shop_id=shop.id).all()
    return templates.TemplateResponse(
        request, "dashboard.html",
        _ctx(request, shop, settings, summary=summary, promotions=promos),
    )


@router.post("/app/mode")
async def set_mode(
    request: Request,
    mode: str = Form(...),
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
):
    try:
        shop.channel_mode = ChannelMode(mode)
    except ValueError:
        raise HTTPException(400, "Invalid channel mode")
    db.commit()
    return RedirectResponse(f"/app{_shop_qs(request)}", status_code=302)


# -- visibility -----------------------------------------------------------

@router.get("/app/visibility", response_class=HTMLResponse)
async def visibility_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    products, collections = await _catalog(shop, settings)
    rules = (
        db.query(VisibilityRule)
        .filter_by(shop_id=shop.id)
        .order_by(VisibilityRule.created_at.desc())
        .all()
    )
    views = services.evaluate_channel(products, shop, db)
    by_gid = {v.product.product_id: v for v in views}
    return templates.TemplateResponse(
        request, "visibility.html",
        _ctx(request, shop, settings, products=products, collections=collections,
             rules=rules, by_gid=by_gid,
             limit=plan_limit(shop, "visibility_rules")),
    )


@router.post("/app/visibility")
async def visibility_add(
    request: Request,
    subject_gids: str = Form(...),  # comma-separated GIDs
    subject_type: str = Form("product"),
    action: str = Form(...),  # hide | show
    reason: str = Form(""),
    db: Session = Depends(get_db),
    shop: Shop = Depends(get_current_shop),
):
    try:
        act = VisibilityAction(action)
    except ValueError:
        raise HTTPException(400, "Invalid action")
    if subject_type not in ("product", "collection"):
        raise HTTPException(400, "Invalid subject type")
    gids = [g.strip() for g in subject_gids.split(",") if g.strip()]
    if not gids:
        raise HTTPException(400, "No products/collections selected")
    for gid in gids:
        check_plan_limit(db, shop, "visibility_rules", VisibilityRule)
        existing = (
            db.query(VisibilityRule)
            .filter_by(shop_id=shop.id, subject_gid=gid, action=act)
            .one_or_none()
        )
        if existing:
            continue
        db.add(
            VisibilityRule(
                shop_id=shop.id,
                subject_type=subject_type,
                subject_gid=gid,
                subject_title=gid.split("/")[-1],
                action=act,
                reason=reason.strip()[:512],
            )
        )
    db.commit()
    return RedirectResponse(f"/app/visibility{_shop_qs(request)}", status_code=302)


@router.post("/app/visibility/{rule_id}/delete")
async def visibility_delete(
    rule_id: int,
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
):
    rule = (
        db.query(VisibilityRule)
        .filter_by(id=rule_id, shop_id=shop.id)
        .one_or_none()
    )
    if not rule:
        raise HTTPException(404, "Rule not found")
    db.delete(rule)
    db.commit()
    return RedirectResponse(f"/app/visibility{_shop_qs(request)}", status_code=302)


# -- pricing --------------------------------------------------------------

def _parse_decimal(raw: str, field: str) -> Decimal:
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(400, f"Invalid {field}")


@router.get("/app/pricing", response_class=HTMLResponse)
async def pricing_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    rules = (
        db.query(PricingRule)
        .filter_by(shop_id=shop.id)
        .order_by(PricingRule.priority.asc(), PricingRule.id.asc())
        .all()
    )
    _, collections = await _catalog(shop, settings)
    return templates.TemplateResponse(
        request, "pricing.html",
        _ctx(request, shop, settings, rules=rules, collections=collections,
             limit=plan_limit(shop, "pricing_rules")),
    )


@router.post("/app/pricing")
async def pricing_add(
    request: Request,
    name: str = Form(...),
    rule_type: str = Form(...),
    scope: str = Form("all"),
    target_gid: str = Form(""),
    target_title: str = Form(""),
    value: str = Form("0"),
    excluded_code: str = Form(""),
    acknowledge_feed_risk: str = Form(""),
    db: Session = Depends(get_db),
    shop: Shop = Depends(get_current_shop),
):
    try:
        rtype = PricingRuleType(rule_type)
        rscope = PricingScope(scope)
    except ValueError:
        raise HTTPException(400, "Invalid rule type or scope")
    if rscope != PricingScope.ALL and not target_gid.strip():
        raise HTTPException(400, "A target product/collection is required for scoped rules")

    amount = Decimal("0")
    code = excluded_code.strip()
    if rtype == PricingRuleType.CODE_EXCLUSION:
        if not code:
            raise HTTPException(400, "Discount code is required for code exclusion")
    else:
        amount = _parse_decimal(value, "value")
        if rtype == PricingRuleType.FLOOR and amount < 0:
            raise HTTPException(400, "Price floor cannot be negative")
        if rtype in (PricingRuleType.MARKUP_PCT, PricingRuleType.DISCOUNT_PCT):
            if amount < 0 or amount > 90:
                raise HTTPException(400, "Percentage must be between 0 and 90")
        # GOOGLE SHOPPING PRICE-MATCH: a discount rule pushes the agent price
        # below the advertised storefront/feed price. That is a price-accuracy
        # violation risk (feed disapproval). Require explicit acknowledgement.
        if rtype == PricingRuleType.DISCOUNT_PCT and amount > 0 and not acknowledge_feed_risk:
            raise HTTPException(
                400,
                "Discount rules lower the agent price below your advertised price, "
                "which risks Google Shopping feed disapproval. Tick the "
                "acknowledgement to proceed, or use an agent-only promotion "
                "(bundle / free shipping) instead — those don't change the "
                "advertised base price.",
            )

    check_plan_limit(db, shop, "pricing_rules", PricingRule)
    db.add(
        PricingRule(
            shop_id=shop.id,
            name=name.strip()[:255] or "Pricing rule",
            enabled=True,
            rule_type=rtype,
            scope=rscope,
            target_gid=target_gid.strip(),
            target_title=target_title.strip()[:512],
            value=amount,
            excluded_code=code,
            priority=0,
        )
    )
    db.commit()
    return RedirectResponse(f"/app/pricing{_shop_qs(request)}", status_code=302)


@router.post("/app/pricing/{rule_id}/toggle")
async def pricing_toggle(
    rule_id: int,
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
):
    rule = db.query(PricingRule).filter_by(id=rule_id, shop_id=shop.id).one_or_none()
    if not rule:
        raise HTTPException(404, "Rule not found")
    rule.enabled = not rule.enabled
    db.commit()
    return RedirectResponse(f"/app/pricing{_shop_qs(request)}", status_code=302)


@router.post("/app/pricing/{rule_id}/delete")
async def pricing_delete(
    rule_id: int,
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
):
    rule = db.query(PricingRule).filter_by(id=rule_id, shop_id=shop.id).one_or_none()
    if not rule:
        raise HTTPException(404, "Rule not found")
    db.delete(rule)
    db.commit()
    return RedirectResponse(f"/app/pricing{_shop_qs(request)}", status_code=302)


# -- promotions -----------------------------------------------------------

@router.get("/app/promotions", response_class=HTMLResponse)
async def promotions_page(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    promos = (
        db.query(AgentPromotion)
        .filter_by(shop_id=shop.id)
        .order_by(AgentPromotion.created_at.desc())
        .all()
    )
    products, _ = await _catalog(shop, settings)
    return templates.TemplateResponse(
        request, "promotions.html",
        _ctx(request, shop, settings, promotions=promos, products=products,
             limit=plan_limit(shop, "promotions")),
    )


@router.post("/app/promotions")
async def promotion_add(
    request: Request,
    name: str = Form(...),
    promo_type: str = Form(...),
    product_gids: str = Form(""),   # bundle: comma-separated
    bundle_price: str = Form(""),   # bundle
    threshold: str = Form(""),      # free shipping
    discount_pct: str = Form("10"),  # Shopify discount backing the promo
    db: Session = Depends(get_db),
    shop: Shop = Depends(get_current_shop),
    settings: Settings = Depends(get_settings_dep),
):
    try:
        ptype = PromotionType(promo_type)
    except ValueError:
        raise HTTPException(400, "Invalid promotion type")
    if ptype == PromotionType.BUNDLE:
        config = {
            "product_gids": [g.strip() for g in product_gids.split(",") if g.strip()],
            "bundle_price": str(_parse_decimal(bundle_price, "bundle price")),
            "currency": "USD",
        }
    else:
        config = {
            "threshold": str(_parse_decimal(threshold, "threshold")),
            "currency": "USD",
        }
    errors = services.validate_promotion_config(ptype, config)
    if errors:
        raise HTTPException(400, "; ".join(errors))
    # Validate the backing discount percent BEFORE any DB mutation so a 400
    # never leaves a half-created promotion behind.
    pct = _parse_decimal(discount_pct, "discount percent")
    if pct < 0 or pct > 90:
        raise HTTPException(400, "Discount percent must be between 0 and 90")

    check_plan_limit(db, shop, "promotions", AgentPromotion)
    promo = AgentPromotion(
        shop_id=shop.id,
        name=name.strip()[:255] or "Agent promotion",
        enabled=True,
        promo_type=ptype,
        config=config,
    )
    db.add(promo)
    db.flush()  # need id for the discount code

    # Sync an unlisted discount code to Shopify so agent checkouts can redeem
    # it. The code is never published on the storefront by this app.
    code = services.promotion_discount_code(promo, shop.shop_domain)
    client = shopify_client()
    minimum = (
        Decimal(config["threshold"]) if ptype == PromotionType.FREE_SHIPPING else None
    )
    result = await client.create_agent_discount_code(
        _token(shop, settings),
        shop.shop_domain,
        title=f"[Agent] {promo.name}",
        code=code,
        discount_pct=pct,
        minimum_order_amount=minimum,
    )
    if not result.ok:
        db.rollback()
        raise HTTPException(502, f"Shopify discount creation failed: {result.error}")
    promo.shopify_discount_id = result.discount_id
    db.commit()
    return RedirectResponse(f"/app/promotions{_shop_qs(request)}", status_code=302)


@router.post("/app/promotions/{promo_id}/toggle")
async def promotion_toggle(
    promo_id: int,
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
):
    promo = db.query(AgentPromotion).filter_by(id=promo_id, shop_id=shop.id).one_or_none()
    if not promo:
        raise HTTPException(404, "Promotion not found")
    promo.enabled = not promo.enabled
    db.commit()
    return RedirectResponse(f"/app/promotions{_shop_qs(request)}", status_code=302)


@router.post("/app/promotions/{promo_id}/delete")
async def promotion_delete(
    promo_id: int,
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    promo = db.query(AgentPromotion).filter_by(id=promo_id, shop_id=shop.id).one_or_none()
    if not promo:
        raise HTTPException(404, "Promotion not found")
    if promo.shopify_discount_id:
        client = shopify_client()
        try:
            await client.delete_discount(
                _token(shop, settings), shop.shop_domain, promo.shopify_discount_id
            )
        except Exception:
            pass  # best-effort cleanup; the local record still goes away
    db.delete(promo)
    db.commit()
    return RedirectResponse(f"/app/promotions{_shop_qs(request)}", status_code=302)


# -- margin guard simulator -----------------------------------------------

@router.get("/app/simulator", response_class=HTMLResponse)
async def simulator(
    request: Request,
    shop: Shop = Depends(get_current_shop),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    products, _ = await _catalog(shop, settings)
    views = services.evaluate_channel(products, shop, db)
    summary = services.summarize(views)
    storefront_total = sum((v.storefront_price for v in views if v.visible), Decimal("0"))
    agent_total = sum((v.agent_price for v in views if v.visible), Decimal("0"))
    promos = (
        db.query(AgentPromotion)
        .filter_by(shop_id=shop.id, enabled=True)
        .all()
    )
    return templates.TemplateResponse(
        request, "simulator.html",
        _ctx(request, shop, settings, views=views, summary=summary,
             storefront_total=storefront_total, agent_total=agent_total,
             promotions=promos),
    )
