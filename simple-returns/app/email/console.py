"""Console email provider: logs instead of sending. Default for dev/mock mode."""
from __future__ import annotations

import logging

log = logging.getLogger("simple_returns.email")


class ConsoleEmailProvider:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, *, to: str, subject: str, html: str, text: str = "") -> None:
        self.sent.append({"to": to, "subject": subject})
        log.info("EMAIL to=%s subject=%s", to, subject)
