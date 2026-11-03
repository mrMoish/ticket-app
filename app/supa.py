"""Тонкий клиент Supabase поверх httpx.

Auth (GoTrue) и данные (PostgREST) вызываются напрямую. Запросы к данным всегда идут
с JWT самого пользователя, поэтому Row Level Security в базе применяется к каждому
запросу. Service-role ключ в приложении не используется вообще.
"""

from __future__ import annotations

from typing import Any

import httpx

TICKET_COLUMNS = "id,title,description,category,status,created_at,updated_at"
LIST_LIMIT = 200


class SupabaseError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


AUTH_MESSAGES = {
    "invalid_credentials": "Неверная почта или пароль.",
    "email_not_confirmed": "Почта ещё не подтверждена. Перейдите по ссылке из письма и войдите снова.",
    "user_already_exists": "Пользователь с такой почтой уже зарегистрирован. Попробуйте войти.",
    "email_exists": "Пользователь с такой почтой уже зарегистрирован. Попробуйте войти.",
    "weak_password": "Пароль слишком простой. Используйте не менее 8 символов, добавьте буквы и цифры.",
    "email_address_invalid": "Укажите корректный адрес электронной почты.",
    "validation_failed": "Проверьте адрес почты и пароль.",
    "signup_disabled": "Регистрация сейчас отключена.",
    "over_request_rate_limit": "Слишком много попыток. Подождите минуту и повторите.",
    "over_email_send_rate_limit": "Слишком много писем отправлено. Попробуйте чуть позже.",
}


def user_message(exc: SupabaseError) -> str:
    """Понятное пользователю объяснение ошибки (без технических деталей)."""
    if exc.status == 0:
        return "Нет связи с сервером. Проверьте интернет-соединение и повторите попытку."
    if exc.status >= 500:
        return "Сервис временно недоступен. Повторите попытку через минуту."
    if exc.code in AUTH_MESSAGES:
        return AUTH_MESSAGES[exc.code]
    if exc.status == 429:
        return AUTH_MESSAGES["over_request_rate_limit"]
    if exc.code == "23514":
        return "Данные не прошли проверку. Проверьте заполнение полей."
    return "Не удалось выполнить запрос. Повторите попытку."


def _error_from(response: httpx.Response) -> SupabaseError:
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    code = str(data.get("error_code") or data.get("code") or data.get("error") or response.status_code)
    message = str(data.get("msg") or data.get("message") or data.get("error_description") or "")
    return SupabaseError(response.status_code, code, message)


def _like_pattern(query: str) -> str:
    # Экранируем спецсимволы LIKE; «*» в PostgREST означает «%», поэтому его убираем.
    safe = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("*", "")
    return f"*{safe}*"


class SupabaseClient:
    def __init__(self, http: httpx.AsyncClient, anon_key: str):
        self.http = http
        self.anon_key = anon_key

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        all_headers = {"apikey": self.anon_key}
        if token:
            all_headers["Authorization"] = f"Bearer {token}"
        if headers:
            all_headers.update(headers)
        try:
            response = await self.http.request(method, path, headers=all_headers, **kwargs)
        except httpx.HTTPError as exc:
            raise SupabaseError(0, "network_error", str(exc)) from exc
        if response.status_code >= 400:
            raise _error_from(response)
        return response

    # --- Auth -------------------------------------------------------------

    async def sign_up(self, email: str, password: str) -> dict[str, Any]:
        r = await self._request("POST", "/auth/v1/signup", json={"email": email, "password": password})
        return r.json()

    async def sign_in(self, email: str, password: str) -> dict[str, Any]:
        r = await self._request(
            "POST",
            "/auth/v1/token",
            params={"grant_type": "password"},
            json={"email": email, "password": password},
        )
        return r.json()

    async def refresh(self, refresh_token: str) -> dict[str, Any]:
        r = await self._request(
            "POST",
            "/auth/v1/token",
            params={"grant_type": "refresh_token"},
            json={"refresh_token": refresh_token},
        )
        return r.json()

    async def sign_out(self, access_token: str) -> None:
        # scope=local завершает только текущую сессию, а не все устройства пользователя
        await self._request("POST", "/auth/v1/logout", token=access_token, params={"scope": "local"})

    # --- Заявки (PostgREST, под JWT пользователя => действует RLS) ----------

    async def list_tickets(self, token: str, *, q: str = "", status: str = "") -> list[dict[str, Any]]:
        params = {"select": TICKET_COLUMNS, "order": "created_at.desc", "limit": str(LIST_LIMIT)}
        if q:
            params["title"] = f"ilike.{_like_pattern(q)}"
        if status:
            params["status"] = f"eq.{status}"
        r = await self._request("GET", "/rest/v1/tickets", token=token, params=params)
        return r.json()

    async def get_ticket(self, token: str, ticket_id: str) -> dict[str, Any] | None:
        params = {"select": TICKET_COLUMNS, "id": f"eq.{ticket_id}"}
        r = await self._request("GET", "/rest/v1/tickets", token=token, params=params)
        rows = r.json()
        return rows[0] if rows else None

    async def create_ticket(self, token: str, ticket_id: str, values: dict[str, str]) -> dict[str, Any]:
        # user_id и status не передаём: их выставляет база (auth.uid() и 'new').
        # id приходит от клиента формы — это ключ идемпотентности против дублей.
        body = {"id": ticket_id, **values}
        r = await self._request(
            "POST",
            "/rest/v1/tickets",
            token=token,
            params={"select": TICKET_COLUMNS},
            headers={"Prefer": "return=representation"},
            json=body,
        )
        return r.json()[0]

    async def update_ticket(self, token: str, ticket_id: str, values: dict[str, str]) -> dict[str, Any] | None:
        r = await self._request(
            "PATCH",
            "/rest/v1/tickets",
            token=token,
            params={"id": f"eq.{ticket_id}", "select": TICKET_COLUMNS},
            headers={"Prefer": "return=representation"},
            json=values,
        )
        rows = r.json()
        return rows[0] if rows else None  # пусто => нет такой заявки или она чужая (RLS)
