from __future__ import annotations

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request

from ..domain import CATEGORIES, SEARCH_MAX, STATUSES, parse_uuid, validate_ticket
from ..session import SessionExpired, UserSession, require_session
from ..supa import SupabaseClient, SupabaseError, user_message
from ..web import redirect, render

router = APIRouter()

NOT_FOUND = "Заявка не найдена или у вас нет к ней доступа."


def _supa(request: Request) -> SupabaseClient:
    return request.app.state.supa


def _guard(exc: SupabaseError) -> None:
    """401 от Supabase = токен недействителен: отправляем на повторный вход."""
    if exc.status == 401:
        raise SessionExpired()


def _save_error_status(exc: SupabaseError) -> int:
    return 503 if exc.status == 0 or exc.status >= 500 else 400


def _short(title: str, limit: int = 60) -> str:
    return title if len(title) <= limit else title[: limit - 1] + "…"


def _list_url(q: str, status: str) -> str:
    params = {}
    if q.strip():
        params["q"] = q.strip()[:SEARCH_MAX]
    if status in STATUSES:
        params["status"] = status
    return "/tickets" + (f"?{urlencode(params)}" if params else "")


# --- Список -----------------------------------------------------------------

@router.get("/tickets")
async def tickets_list(
    request: Request,
    q: str = "",
    status: str = "",
    session: UserSession = Depends(require_session),
):
    q = q.strip()[:SEARCH_MAX]
    status_filter = status if status in STATUSES else ""
    tickets: list[dict] = []
    error = None
    try:
        tickets = await _supa(request).list_tickets(session.access_token, q=q, status=status_filter)
    except SupabaseError as exc:
        _guard(exc)
        error = f"Не удалось загрузить заявки. {user_message(exc)}"
    return render(
        request,
        "tickets.html",
        {
            "tickets": tickets,
            "q": q,
            "status_filter": status_filter,
            "filtered": bool(q or status_filter),
            "error": error,
        },
    )


# --- Создание ---------------------------------------------------------------

@router.get("/tickets/new")
async def new_ticket_page(request: Request, session: UserSession = Depends(require_session)):
    return render(
        request,
        "ticket_form.html",
        {"mode": "create", "form": {}, "errors": {}, "client_id": str(uuid.uuid4())},
    )


@router.post("/tickets")
async def create_ticket(
    request: Request,
    title: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    client_id: str = Form(""),
    session: UserSession = Depends(require_session),
):
    # client_id генерируется при показе формы и служит ключом идемпотентности:
    # повторная отправка той же формы упрётся в первичный ключ и не создаст дубль.
    ticket_id = parse_uuid(client_id) or str(uuid.uuid4())
    values, errors = validate_ticket(title, description, category)
    if errors:
        return render(
            request,
            "ticket_form.html",
            {"mode": "create", "form": values, "errors": errors, "client_id": ticket_id},
            422,
        )

    supa = _supa(request)
    try:
        ticket = await supa.create_ticket(session.access_token, ticket_id, values)
    except SupabaseError as exc:
        _guard(exc)
        ticket = None
        if exc.code == "23505":  # такая заявка уже создана этой же отправкой формы
            try:
                ticket = await supa.get_ticket(session.access_token, ticket_id)
            except SupabaseError as exc2:
                _guard(exc2)
        if ticket is None:
            return render(
                request,
                "ticket_form.html",
                {
                    "mode": "create",
                    "form": values,
                    "errors": {},
                    "client_id": ticket_id,
                    "error": f"Заявка не сохранена. {user_message(exc)} Введённые данные остались в форме.",
                },
                _save_error_status(exc),
            )
    return redirect("/tickets", flash=("success", f"Заявка «{_short(ticket['title'])}» создана."))


# --- Просмотр и редактирование ---------------------------------------------------

async def _load(request: Request, session: UserSession, raw_id: str) -> tuple[str | None, dict | None]:
    ticket_id = parse_uuid(raw_id)
    if not ticket_id:
        return None, None
    try:
        return ticket_id, await _supa(request).get_ticket(session.access_token, ticket_id)
    except SupabaseError as exc:
        _guard(exc)
        raise


@router.get("/tickets/{ticket_id}")
async def edit_ticket_page(request: Request, ticket_id: str, session: UserSession = Depends(require_session)):
    try:
        _, ticket = await _load(request, session, ticket_id)
    except SupabaseError as exc:
        return redirect("/tickets", flash=("error", f"Не удалось открыть заявку. {user_message(exc)}"))
    if ticket is None:
        return redirect("/tickets", flash=("error", NOT_FOUND))
    return render(request, "ticket_form.html", {"mode": "edit", "ticket": ticket, "form": ticket, "errors": {}})


@router.post("/tickets/{ticket_id}")
async def update_ticket(
    request: Request,
    ticket_id: str,
    title: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    status: str = Form(""),
    session: UserSession = Depends(require_session),
):
    try:
        tid, ticket = await _load(request, session, ticket_id)
    except SupabaseError as exc:
        return redirect("/tickets", flash=("error", f"Не удалось открыть заявку. {user_message(exc)}"))
    if ticket is None:
        return redirect("/tickets", flash=("error", NOT_FOUND))

    values, errors = validate_ticket(title, description, category, status)
    if errors:
        return render(request, "ticket_form.html", {"mode": "edit", "ticket": ticket, "form": values, "errors": errors}, 422)

    try:
        updated = await _supa(request).update_ticket(session.access_token, tid, values)
    except SupabaseError as exc:
        _guard(exc)
        return render(
            request,
            "ticket_form.html",
            {
                "mode": "edit",
                "ticket": ticket,
                "form": values,
                "errors": {},
                "error": f"Изменения не сохранены. {user_message(exc)} Введённые данные остались в форме.",
            },
            _save_error_status(exc),
        )
    if updated is None:
        return redirect("/tickets", flash=("error", NOT_FOUND))
    return redirect(f"/tickets/{tid}", flash=("success", "Изменения сохранены."))


# --- Быстрая смена статуса из списка -------------------------------------------

@router.post("/tickets/{ticket_id}/status")
async def change_status(
    request: Request,
    ticket_id: str,
    status: str = Form(""),
    q: str = Form(""),
    f: str = Form(""),
    session: UserSession = Depends(require_session),
):
    back = _list_url(q, f)
    tid = parse_uuid(ticket_id)
    if not tid or status not in STATUSES:
        return redirect(back, flash=("error", "Статус не изменён: выберите значение из списка."))
    try:
        updated = await _supa(request).update_ticket(session.access_token, tid, {"status": status})
    except SupabaseError as exc:
        _guard(exc)
        return redirect(back, flash=("error", f"Статус не изменён. {user_message(exc)}"))
    if updated is None:
        return redirect(back, flash=("error", NOT_FOUND))
    return redirect(
        back,
        flash=("success", f"Статус заявки «{_short(updated['title'])}» изменён на «{STATUSES[status]}»."),
    )
