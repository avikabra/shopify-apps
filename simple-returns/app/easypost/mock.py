"""Fixture shipping provider: returns a fake label, records calls."""
from __future__ import annotations

import itertools

from .protocol import Address, Parcel, ShippingLabel


class MockShippingProvider:
    def __init__(self):
        self._counter = itertools.count(5000)
        self.labels: list[dict] = []

    async def create_return_label(
        self,
        *,
        to_address: Address,
        from_address: Address,
        parcel: Parcel,
        reference: str = "",
    ) -> ShippingLabel:
        n = next(self._counter)
        label = ShippingLabel(
            id=f"shp_mock_{n}",
            tracking_number=f"1Z999MOCK{n}",
            label_url=f"https://example.com/labels/mock_{n}.pdf",
            carrier="MockCarrier",
            service="Ground",
            cost_amount="6.95",
            cost_currency="USD",
        )
        self.labels.append(
            {
                "reference": reference,
                "to": to_address.zip,
                "from": from_address.zip,
                "label": label,
            }
        )
        return label
