from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import Response

ACCESS_COOKIE = "sb_access"
REFRESH_COOKIE = "sb_refresh"
REFRESH_MAX_AGE = 60 * 60 * 24 * 30


class LoginRequired(Exception):
    """Страница доступна только после входа."""


class SessionExpired(Exception):
    """Supabase отклонил токен: нужен повторный вход."""


@dataclass(frozen=True)
class UserSession:
    access_token: str
    user_id: str
    email: str


def _claims(token: str) -> dict[str, Any] | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data if isinstance(data, dict) else None
    except (IndexError, ValueError):
        return None


def session_from_token(token: str | None, skew: int = 30) -> UserSession | None:
    """Достаёт пользователя из JWT. Подпись здесь не проверяется намеренно: от неё не зависит
    доступ к данным — каждый запрос к Supabase проверяет токен сам, а RLS режет строки по auth.uid().
    Токен, который истёк или скоро истечёт, считаем отсутствующим (дальше сработает refresh)."""
    if not token:
        return None
    claims = _claims(token)
    if not claims or not claims.get("sub"):
        return None
    if float(claims.get("exp", 0)) - time.time() < skew:
        return None
    return UserSession(access_token=token, user_id=str(claims["sub"]), email=str(claims.get("email", "")))


def set_auth_cookies(response: Response, tokens: dict[str, Any], secure: bool) -> None:
    common = {"httponly": True, "samesite": "lax", "secure": secure, "path": "/"}
    response.set_cookie(
        ACCESS_COOKIE, tokens["access_token"], max_age=max(int(tokens.get("expires_in", 3600)), 60), **common
    )
    response.set_cookie(REFRESH_COOKIE, tokens["refresh_token"], max_age=REFRESH_MAX_AGE, **common)


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


def require_session(request: Request) -> UserSession:
    session = getattr(request.state, "session", None)
    if session is None:
        raise LoginRequired()
    return session
