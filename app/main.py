from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .routes import auth, tickets
from .session import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    LoginRequired,
    SessionExpired,
    clear_auth_cookies,
    session_from_token,
    set_auth_cookies,
)
from .supa import SupabaseClient, SupabaseError
from .web import BASE_DIR, redirect


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """settings/transport можно подменить в тестах; настройки из .env читаются при старте."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = settings or get_settings()
        async with httpx.AsyncClient(base_url=cfg.supabase_url, timeout=10.0, transport=transport) as http:
            app.state.settings = cfg
            app.state.supa = SupabaseClient(http, cfg.supabase_anon_key)
            yield

    app = FastAPI(title="Заявки на автоматизацию", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    app.include_router(auth.router)
    app.include_router(tickets.router)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        return redirect("/login")

    @app.exception_handler(SessionExpired)
    async def _session_expired(request: Request, exc: SessionExpired):
        response = redirect("/login", flash=("error", "Сессия истекла. Войдите снова."))
        clear_auth_cookies(response)
        return response

    @app.middleware("http")
    async def attach_session(request: Request, call_next):
        if request.url.path.startswith("/static"):
            return await call_next(request)

        session = session_from_token(request.cookies.get(ACCESS_COOKIE))
        new_tokens = None
        drop_cookies = False
        refresh_token = request.cookies.get(REFRESH_COOKIE)

        if session is None and refresh_token:
            # Access-токен истёк: тихо обновляем сессию по refresh-токену.
            try:
                new_tokens = await request.app.state.supa.refresh(refresh_token)
                session = session_from_token(new_tokens.get("access_token"))
            except SupabaseError as exc:
                drop_cookies = exc.status != 0  # при сбое сети cookies не трогаем
            if session is None:
                new_tokens = None

        request.state.session = session
        response = await call_next(request)

        route_set_cookies = any(
            h.startswith((f"{ACCESS_COOKIE}=", f"{REFRESH_COOKIE}=")) for h in response.headers.getlist("set-cookie")
        )
        if not route_set_cookies:  # логин/выход сами управляют cookies
            if new_tokens:
                set_auth_cookies(response, new_tokens, request.app.state.settings.cookie_secure)
            elif drop_cookies:
                clear_auth_cookies(response)

        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    return app


app = create_app()
