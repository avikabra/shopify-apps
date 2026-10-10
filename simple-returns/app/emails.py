"""Customer + merchant email templates (plain functions, no framework magic)."""
from __future__ import annotations


def request_received(brand: str, order_name: str, request_id: int) -> tuple[str, str]:
    subject = f"Your return request for {order_name} was received"
    html = f"""<p>Hi,</p>
<p>We've received your return request for order <strong>{order_name}</strong>
(reference #{request_id}). We'll email you as soon as it's reviewed.</p>
<p>Thanks,<br>{brand}</p>"""
    return subject, html


def request_approved(brand: str, order_name: str, label_url: str, tracking: str) -> tuple[str, str]:
    subject = f"Your return for {order_name} is approved — label inside"
    html = f"""<p>Hi,</p>
<p>Good news: your return for order <strong>{order_name}</strong> is approved.</p>
<p><a href="{label_url}">Download your return shipping label</a>
(tracking: {tracking}). Please ship the items within 14 days.</p>
<p>Thanks,<br>{brand}</p>"""
    return subject, html


def request_approved_label_pending(brand: str, order_name: str) -> tuple[str, str]:
    subject = f"Your return for {order_name} is approved"
    html = f"""<p>Hi,</p>
<p>Good news: your return for order <strong>{order_name}</strong> is approved.
We're preparing your return shipping label and will email it to you shortly.</p>
<p>Thanks,<br>{brand}</p>"""
    return subject, html


def request_rejected(brand: str, order_name: str, note: str) -> tuple[str, str]:
    subject = f"Update on your return request for {order_name}"
    html = f"""<p>Hi,</p>
<p>We weren't able to approve your return request for order
<strong>{order_name}</strong>.</p>
<p>{note or 'Please contact support if you believe this is a mistake.'}</p>
<p>Thanks,<br>{brand}</p>"""
    return subject, html


def refund_issued(brand: str, order_name: str, amount: str, currency: str) -> tuple[str, str]:
    subject = f"Refund issued for {order_name}"
    html = f"""<p>Hi,</p>
<p>We've issued a refund of <strong>{amount} {currency}</strong> for order
<strong>{order_name}</strong> to your original payment method. It can take
5–10 business days to appear on your statement.</p>
<p>Thanks,<br>{brand}</p>"""
    return subject, html


def store_credit_issued(brand: str, order_name: str, amount: str, currency: str, code: str) -> tuple[str, str]:
    subject = f"Store credit issued for {order_name}"
    html = f"""<p>Hi,</p>
<p>We've issued <strong>{amount} {currency}</strong> in store credit for order
<strong>{order_name}</strong>.</p>
<p>Your gift card code: <strong>{code}</strong><br>
Enter it at checkout on your next purchase.</p>
<p>Thanks,<br>{brand}</p>"""
    return subject, html


def exchange_sent(brand: str, order_name: str, invoice_url: str | None) -> tuple[str, str]:
    subject = f"Your exchange for {order_name} is ready"
    link = f'<p><a href="{invoice_url}">Complete your exchange order here</a>.</p>' if invoice_url else ""
    html = f"""<p>Hi,</p>
<p>Your exchange for order <strong>{order_name}</strong> is ready.</p>
{link}
<p>Thanks,<br>{brand}</p>"""
    return subject, html


def merchant_new_request(brand: str, order_name: str, request_id: int, app_url: str) -> tuple[str, str]:
    subject = f"New return request {order_name} needs review"
    html = f"""<p>A new return request for order <strong>{order_name}</strong>
needs your review.</p>
<p><a href="{app_url}/app/requests/{request_id}">Review it in Simple Returns</a></p>"""
    return subject, html
