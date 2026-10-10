"""Email provider seam."""
from __future__ import annotations

from typing import Protocol


class EmailError(Exception):
    pass


class EmailProvider(Protocol):
    async def send(self, *, to: str, subject: str, html: str, text: str = "") -> None:
        ...
