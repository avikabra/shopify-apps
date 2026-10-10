"""Customer portal API (called by the theme app extension).

Unauthenticated by design: the order number + email pair IS the credential
(the customer can only see/mutate their own order). Protected by:
  - dynamic CORS (Origin must be the shop's storefront domain),
  - a light in-memory rate limit (abuse mitigation, not a security boundary).
"""
from __future__ import annotations

import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..deps import email_provider, get_db, get_settings_dep, shipping_provider, shopify_client
from ..models import Shop
from ..services import PlanLimitExceeded, ReturnService, VALID_REASONS

router = APIRouter(prefix="/portal")

# -- rate limiting (in-memory, per process) --------------------------------
_RATE_BUCKETS: dict[str, list[float]] = defaultdict(list)
_RATE_LIMIT = 60  # requests
_RATE_WINDOW = 60.0  # per seconds


def rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    bucket = _RATE_BUCKETS[ip]
    while bucket and bucket[0] < now - _RATE_WINDOW:
        bucket.pop(0)
    if len(bucket) >= _RATE_LIMIT:
        raise HTTPException(429, "Too many requests, please slow down.")
    bucket.append(now)


def get_portal_shop(shop_domain: str, db: Session) -> Shop:
    shop = (
        db.query(Shop)
        .filter_by(shop_domain=shop_domain.strip().lower())
        .one_or_none()
    )
    if not shop or shop.uninstalled_at:
        raise HTTPException(404, "Store not found")
    return shop


def check_portal_origin(request: Request, shop: Shop, settings: Settings) -> None:
    """Allow the shop's storefront origin(s); block everything else."""
    origin = request.headers.get("origin")
    if not origin:
        return  # non-browser callers (curl, server-to-server)
    if settings.use_mock_shopify:
        return
    host = origin.split("://")[-1].split("/")[0].lower()
    allowed = {shop.shop_domain.lower()}
    extra = (shop.settings or {}).get("custom_domains") or ""
    allowed |= {d.strip().lower() for d in extra.split(",") if d.strip()}
    if host not in allowed:
        raise HTTPException(403, "Origin not allowed")


# -- schemas ----------------------------------------------------------------

class LookupIn(BaseModel):
    shop: str
    order_name: str
    email: str


class VariantsIn(BaseModel):
    shop: str
    product_id: str


class SubmitItem(BaseModel):
    line_item_id: str
    quantity: int = Field(ge=1)
    reason: str
    note: str = ""
    exchange_variant_id: str | None = None


class SubmitIn(BaseModel):
    shop: str
    order_name: str
    email: str
    resolution: str
    items: list[SubmitItem] = Field(min_length=1)


# -- routes ------------------------------------------------------------------

@router.get("/reasons")
async def list_reasons():
    labels = {
        "DEFECTIVE": "Defective / damaged",
        "WRONG_ITEM": "Wrong item sent",
        "NOT_AS_DESCRIBED": "Not as described",
        "SIZE_TOO_SMALL": "Too small",
        "SIZE_TOO_LARGE": "Too large",
        "STYLE": "Don't like the style",
        "COLOR": "Don't like the color",
        "UNWANTED": "Changed my mind",
        "OTHER": "Other",
    }
    return {"reasons": [{"value": v, "label": labels.get(v, v)} for v in sorted(VALID_REASONS) if v in labels]}


@router.post("/lookup", dependencies=[Depends(rate_limit)])
async def lookup_order(
    body: LookupIn,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    shop = get_portal_shop(body.shop, db)
    check_portal_origin(request, shop, settings)
    order = await shopify_client().get_order_for_return(
        shop.shop_domain, body.order_name, body.email
    )
    if order is None:
        # Same response for "no such order" and "wrong email": no enumeration.
        raise HTTPException(404, "We couldn't find that order. Check the number and email.")
    return {
        "order_name": order.order_name,
        "customer_name": order.customer_name,
        "currency": order.currency,
        "items": [
            {
                "line_item_id": li.line_item_id,
                "product_id": li.product_id,
                "title": li.title,
                "variant_title": li.variant_title,
                "sku": li.sku,
                "quantity": li.quantity,
                "unit_price": str(li.unit_price),
                "eligible": li.fulfillment_line_item_id is not None,
            }
            for li in order.line_items
        ],
    }


@router.post("/variants", dependencies=[Depends(rate_limit)])
async def product_variants(
    body: VariantsIn,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    shop = get_portal_shop(body.shop, db)
    check_portal_origin(request, shop, settings)
    variants = await shopify_client().get_product_variants(shop.shop_domain, body.product_id)
    return {
        "variants": [
            {"variant_id": v.variant_id, "title": v.title, "sku": v.sku, "price": str(v.price)}
            for v in variants
        ]
    }


@router.post("/submit", dependencies=[Depends(rate_limit)])
async def submit_return(
    body: SubmitIn,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
):
    shop = get_portal_shop(body.shop, db)
    check_portal_origin(request, shop, settings)
    order = await shopify_client().get_order_for_return(
        shop.shop_domain, body.order_name, body.email
    )
    if order is None:
        raise HTTPException(404, "We couldn't find that order. Check the number and email.")
    svc = ReturnService(
        db=db,
        shopify=shopify_client(),
        shipping=shipping_provider(),
        email=email_provider(),
        app_url=settings.app_url,
    )
    try:
        req = await svc.submit_request(
            shop,
            order,
            [i.model_dump() for i in body.items],
            body.resolution,
        )
    except PlanLimitExceeded:
        raise HTTPException(503, "Returns are temporarily unavailable. Please contact the store.")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {
        "request_id": req.id,
        "status": req.status.value,
        "label_url": req.label_url,
    }
