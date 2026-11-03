from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .domain import CATEGORIES, STATUSES

BASE_DIR = Path(__file__).resolve().parent
FLASH_COOKIE = "flash"

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _parse_dt(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def dt_iso(value: Any) -> str:
    dt = _parse_dt(value)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def dt_text(value: Any) -> str:
    # Запасной вариант без JS; в браузере app.js заменяет его на местное время.
    dt = _parse_dt(value)
    return dt.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M (UTC)") if dt else str(value)


templates.env.filters["dt_iso"] = dt_iso
templates.env.filters["dt_text"] = dt_text


# --- Flash-сообщения: переживают один редирект ---------------------------------

def _encode_flash(kind: str, text: str) -> str:
    raw = json.dumps({"kind": kind, "text": text}, ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_flash(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        kind = "error" if data.get("kind") == "error" else "success"
        return {"kind": kind, "text": str(data.get("text", ""))[:500]}
    except (ValueError, TypeError, AttributeError):
        return None


def redirect(url: str, flash: tuple[str, str] | None = None) -> RedirectResponse:
    """303 (Post/Redirect/Get): обновление страницы после сохранения не отправляет форму повторно."""
    response = RedirectResponse(url, status_code=303)
    if flash:
        response.set_cookie(
            FLASH_COOKIE, _encode_flash(*flash), max_age=60, httponly=True, samesite="lax", path="/"
        )
    return response


def render(request: Request, name: str, context: dict[str, Any] | None = None, status_code: int = 200) -> Response:
    flash = _decode_flash(request.cookies.get(FLASH_COOKIE))
    ctx = {
        "session": getattr(request.state, "session", None),
        "flash": flash,
        "categories": CATEGORIES,
        "statuses": STATUSES,
        **(context or {}),
    }
    response = templates.TemplateResponse(request, name, ctx, status_code=status_code)
    if flash is not None or request.cookies.get(FLASH_COOKIE):
        response.delete_cookie(FLASH_COOKIE, path="/")
    return response
