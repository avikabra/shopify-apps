"""Real EasyPost REST client.

EasyPost API: POST https://api.easypost.com/v2/shipments with HTTP Basic auth
(username = API key). We create the shipment, buy the cheapest available rate
(optionally restricted to the merchant's carrier accounts), and return the
label + tracking number.
"""
from __future__ import annotations

import httpx

from ..config import get_settings
from .protocol import Address, Parcel, ShippingError, ShippingLabel

_API_BASE = "https://api.easypost.com/v2"


def _addr(a: Address) -> dict:
    return {
        "name": a.name,
        "street1": a.street1,
        "street2": a.street2 or None,
        "city": a.city,
        "state": a.state,
        "zip": a.zip,
        "country": a.country,
        "phone": a.phone or None,
        "email": a.email or None,
    }


class EasyPostClient:
    def __init__(self, timeout: float = 25.0):
        self._settings = get_settings()
        self._timeout = timeout

    async def create_return_label(
        self,
        *,
        to_address: Address,
        from_address: Address,
        parcel: Parcel,
        reference: str = "",
    ) -> ShippingLabel:
        settings = self._settings
        if not settings.easypost_api_key:
            raise ShippingError("EASYPOST_API_KEY is not configured.")
        carrier_accounts = [
            c.strip()
            for c in settings.easypost_carrier_accounts.split(",")
            if c.strip()
        ]
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            try:
                # 1. Create shipment (is_return=True) and fetch rates.
                r = await http.post(
                    f"{_API_BASE}/shipments",
                    auth=(settings.easypost_api_key, ""),
                    json={
                        "shipment": {
                            "to_address": _addr(to_address),
                            "from_address": _addr(from_address),
                            "parcel": {
                                "weight": parcel.weight_oz,
                                "length": parcel.length_in,
                                "width": parcel.width_in,
                                "height": parcel.height_in,
                            },
                            "is_return": True,
                            "reference": reference or None,
                        }
                    },
                )
                r.raise_for_status()
                shipment = r.json()
                shipment_id = shipment["id"]
                rates = shipment.get("rates") or []
                if carrier_accounts:
                    rates = [
                        x for x in rates if x.get("carrier_account_id") in carrier_accounts
                    ]
                if not rates:
                    raise ShippingError("EasyPost returned no usable rates.")
                cheapest = min(rates, key=lambda x: float(x["rate"]))

                # 2. Buy the cheapest rate.
                b = await http.post(
                    f"{_API_BASE}/shipments/{shipment_id}/buy",
                    auth=(settings.easypost_api_key, ""),
                    json={"rate": {"id": cheapest["id"]}},
                )
                b.raise_for_status()
                bought = b.json()
            except httpx.HTTPError as exc:
                raise ShippingError(f"EasyPost request failed: {exc}") from exc

        return ShippingLabel(
            id=bought["id"],
            tracking_number=bought.get("tracking_code") or "",
            label_url=(bought.get("postage_label") or {}).get("label_url") or "",
            carrier=cheapest.get("carrier") or "",
            service=cheapest.get("service") or "",
            cost_amount=cheapest.get("rate") or "0",
            cost_currency=cheapest.get("currency") or "USD",
        )
