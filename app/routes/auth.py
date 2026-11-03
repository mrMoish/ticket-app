from __future__ import annotations

import re

from fastapi import APIRouter, Form, Request

from ..session import clear_auth_cookies, set_auth_cookies
from ..supa import SupabaseClient, SupabaseError, user_message
from ..web import redirect, render

router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8


def _supa(request: Request) -> SupabaseClient:
    return request.app.state.supa


def _secure(request: Request) -> bool:
    return request.app.state.settings.cookie_secure


def _error_status(exc: SupabaseError) -> int:
    return 503 if exc.status == 0 or exc.status >= 500 else 400


@router.get("/")
async def index(request: Request):
    return redirect("/tickets" if request.state.session else "/login")


@router.get("/login")
async def login_page(request: Request):
    if request.state.session:
        return redirect("/tickets")
    return render(request, "login.html")


@router.post("/login")
async def login(request: Request, email: str = Form(""), password: str = Form("")):
    email = email.strip().lower()
    if not EMAIL_RE.match(email) or not password:
        return render(request, "login.html", {"email": email, "error": "Введите почту и пароль."}, 422)
    try:
        tokens = await _supa(request).sign_in(email, password)
    except SupabaseError as exc:
        return render(request, "login.html", {"email": email, "error": user_message(exc)}, _error_status(exc))
    response = redirect("/tickets")
    set_auth_cookies(response, tokens, _secure(request))
    return response


@router.get("/register")
async def register_page(request: Request):
    if request.state.session:
        return redirect("/tickets")
    return render(request, "register.html")


@router.post("/register")
async def register(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
):
    email = email.strip().lower()
    errors: dict[str, str] = {}
    if not EMAIL_RE.match(email):
        errors["email"] = "Укажите корректный адрес электронной почты."
    if len(password) < MIN_PASSWORD:
        errors["password"] = f"Пароль должен быть не короче {MIN_PASSWORD} символов."
    elif password != password2:
        errors["password2"] = "Пароли не совпадают."
    if errors:
        return render(request, "register.html", {"email": email, "errors": errors}, 422)

    try:
        data = await _supa(request).sign_up(email, password)
    except SupabaseError as exc:
        return render(
            request, "register.html", {"email": email, "error": user_message(exc)}, _error_status(exc)
        )

    if data.get("access_token"):
        # Подтверждение почты в проекте выключено: сразу входим.
        response = redirect("/tickets", flash=("success", "Аккаунт создан. Добро пожаловать!"))
        set_auth_cookies(response, data, _secure(request))
        return response

    user = data.get("user", data)
    if user.get("identities") == []:
        # При включённом подтверждении Supabase не выдаёт ошибку для уже занятой почты,
        # а возвращает «пустого» пользователя без identities.
        return render(
            request,
            "register.html",
            {"email": email, "error": "Пользователь с такой почтой уже зарегистрирован. Попробуйте войти."},
            400,
        )

    return redirect(
        "/login",
        flash=("success", "Аккаунт создан. Мы отправили письмо — подтвердите почту и войдите."),
    )


@router.post("/logout")
async def logout(request: Request):
    session = request.state.session
    if session:
        try:
            await _supa(request).sign_out(session.access_token)
        except SupabaseError:
            pass  # токен всё равно удаляем у пользователя
    response = redirect("/login", flash=("success", "Вы вышли из аккаунта."))
    clear_auth_cookies(response)
    return response
