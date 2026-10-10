"""Shipping-label provider seam. Pass-through cost to the merchant."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Address:
    name: str
    street1: str
    street2: str = ""
    city: str = ""
    state: str = ""
    zip: str = ""
    country: str = "US"
    phone: str = ""
    email: str = ""


@dataclass
class Parcel:
    weight_oz: float = 16.0
    length_in: float = 12.0
    width_in: float = 9.0
    height_in: float = 6.0


@dataclass
class ShippingLabel:
    id: str
    tracking_number: str
    label_url: str
    carrier: str
    service: str
    cost_amount: str
    cost_currency: str = "USD"


class ShippingError(Exception):
    pass


class ShippingProvider(Protocol):
    async def create_return_label(
        self,
        *,
        to_address: Address,    # merchant warehouse (ship TO)
        from_address: Address,   # customer (ship FROM)
        parcel: Parcel,
        reference: str = "",    # our return request id, for reconciliation
    ) -> ShippingLabel:
        """Buy a return shipping label. Cost is passed through to the merchant."""
        ...
