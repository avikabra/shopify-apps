"""SMTP email provider for production (works with any SMTP relay)."""
from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import get_settings
from .protocol import EmailError


class SMTPEmailProvider:
    async def send(self, *, to: str, subject: str, html: str, text: str = "") -> None:
        settings = get_settings()
        if not settings.smtp_host:
            raise EmailError("SMTP_HOST is not configured.")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.email_from
        msg["To"] = to
        if text:
            msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
                s.starttls()
                if settings.smtp_username:
                    s.login(settings.smtp_username, settings.smtp_password)
                s.sendmail(settings.email_from, [to], msg.as_string())
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailError(f"SMTP send failed: {exc}") from exc
