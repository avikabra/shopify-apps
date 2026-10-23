"""Public privacy policy page (no auth required).

Serves the repo-level PRIVACY_POLICY.md as HTML at GET /privacy.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

# PRIVACY_POLICY.md lives at the monorepo root: <repo>/PRIVACY_POLICY.md,
# i.e. three levels up from this file (app/privacy.py -> app -> <app> -> repo).
# The deployed service root is the app directory itself, so bundle a copy of
# the policy there; the monorepo root copy is the fallback for local runs.
_POLICY_CANDIDATES = [
    Path(__file__).resolve().parents[1] / "PRIVACY_POLICY.md",
    Path(__file__).resolve().parents[2] / "PRIVACY_POLICY.md",
]
_POLICY_PATH = next((p for p in _POLICY_CANDIDATES if p.exists()), _POLICY_CANDIDATES[0])


def _md_to_html(md: str) -> str:
    """Minimal Markdown -> HTML for the privacy policy's constructs."""
    out: list[str] = []
    in_list = False
    for raw_line in md.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        # Headings
        m = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if m:
            if in_list:
                out.append("</ul>")
                in_list = False
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue
        # Bullets
        if stripped.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        out.append(f"<p>{_inline(stripped)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text
    )
    return text


@router.get("/privacy", response_class=HTMLResponse)
async def privacy_policy():
    try:
        md = _POLICY_PATH.read_text(encoding="utf-8")
        body = _md_to_html(md)
    except OSError:
        body = "<p>Privacy policy is currently unavailable.</p>"
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Privacy Policy</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;padding:0 1rem;line-height:1.6}h1{font-size:1.6rem}h2{font-size:1.25rem;margin-top:1.5rem}</style>"
        f"</head><body>{body}</body></html>"
    )
