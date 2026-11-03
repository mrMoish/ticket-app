from __future__ import annotations

import re

from .conftest import FakeSupabase, create_ticket, make_jwt, new_form_id, register


def ticket_id_from_list(html: str) -> str:
    return re.search(r'href="/tickets/([0-9a-f-]{36})"', html).group(1)


# --- Аккаунт -----------------------------------------------------------------

def test_protected_pages_redirect_to_login(client):
    r = client.get("/tickets", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_register_logs_in_and_logout_closes_access(client):
    r = register(client)
    assert r.status_code == 200 and "Аккаунт создан" in r.text and "Мои заявки" in r.text

    r = client.post("/logout")
    assert "Вы вышли из аккаунта" in r.text
    assert client.get("/tickets", follow_redirects=False).status_code == 303


def test_register_validation_and_duplicate_email(client):
    r = client.post("/register", data={"email": "bad", "password": "short", "password2": "short"})
    assert r.status_code == 422 and "корректный адрес" in r.text and "не короче 8" in r.text

    register(client)
    client.cookies.clear()
    r = register(client)
    assert "уже зарегистрирован" in r.text


def test_register_with_email_confirmation_flow(client, fake):
    fake.confirm_email = True
    r = register(client, "c@example.com")
    assert "подтвердите почту" in r.text and "Вход" in r.text
    r = register(client, "c@example.com")  # повторная регистрация той же почты
    assert "уже зарегистрирован" in r.text


def test_login_wrong_password_shows_clear_error(client):
    register(client)
    client.cookies.clear()
    r = client.post("/login", data={"email": "a@example.com", "password": "wrong-password"})
    assert r.status_code == 400 and "Неверная почта или пароль" in r.text


def test_login_again_shows_saved_tickets(client):
    register(client)
    create_ticket(client, title="Сохранится после входа")
    client.post("/logout")
    r = client.post("/login", data={"email": "a@example.com", "password": "password123"})
    assert "Сохранится после входа" in r.text


# --- Создание заявки -----------------------------------------------------------------

def test_create_ticket_has_status_new_and_is_listed(client, fake):
    register(client)
    r = create_ticket(client, title="Интеграция CRM", category="integration")
    assert "создана" in r.text and "Интеграция CRM" in r.text
    assert "Интеграция" in r.text and '<option value="new" selected>' in r.text
    # приложение не передаёт ни user_id, ни status: их ставит база
    assert set(fake.last_insert_body) == {"id", "title", "description", "category"}


def test_all_fields_required_and_nothing_saved(client, fake):
    register(client)
    r = client.post("/tickets", data={"title": "  ", "description": "", "category": "", "client_id": new_form_id(client)})
    assert r.status_code == 422
    assert "Введите название" in r.text and "Опишите задачу" in r.text and "Выберите категорию" in r.text
    assert fake.tickets == {}

    r = create_ticket(client, category="hack")
    assert r.status_code == 422 and "из списка" in r.text


def test_double_submit_does_not_create_duplicates(client, fake):
    register(client)
    form_id = new_form_id(client)
    r1 = create_ticket(client, client_id=form_id)
    r2 = create_ticket(client, client_id=form_id)  # тот же client_id = повторное нажатие
    assert "создана" in r1.text and "создана" in r2.text
    assert len(fake.tickets) == 1 and fake.insert_attempts == 2


def test_save_failure_keeps_form_data_and_explains(client, fake):
    register(client)
    fake.fail_next_writes = 1
    r = create_ticket(client, title="Не потеряется")
    assert r.status_code == 503
    assert "Заявка не сохранена" in r.text and "Не потеряется" in r.text
    assert fake.tickets == {}


# --- Список, поиск, фильтр ---------------------------------------------------------

def test_search_and_status_filter(client):
    register(client)
    create_ticket(client, title="Бот для WhatsApp")
    create_ticket(client, title="Отчёт по продажам", category="report")
    html = client.get("/tickets").text
    tid = re.findall(r'href="/tickets/([0-9a-f-]{36})"', html)[0]  # самая новая — «Отчёт…»
    client.post(f"/tickets/{tid}/status", data={"status": "done"})

    assert "WhatsApp" in client.get("/tickets", params={"q": "whats"}).text
    assert "Отчёт по продажам" not in client.get("/tickets", params={"q": "whats"}).text

    done = client.get("/tickets", params={"status": "done"}).text
    assert "Отчёт по продажам" in done and "WhatsApp" not in done

    empty = client.get("/tickets", params={"q": "нет такого"}).text
    assert "Ничего не найдено" in empty

    # спецсимволы LIKE не ломают запрос и не работают как маска
    r = client.get("/tickets", params={"q": "100%_*"})
    assert r.status_code == 200 and "Ничего не найдено" in r.text


# --- Редактирование и статус -----------------------------------------------------------

def test_edit_ticket_and_persist(client):
    register(client)
    create_ticket(client, title="Старое название")
    tid = ticket_id_from_list(client.get("/tickets").text)

    page = client.get(f"/tickets/{tid}")
    assert "Старое название" in page.text and "Редактирование заявки" in page.text

    r = client.post(
        f"/tickets/{tid}",
        data={"title": "Новое название", "description": "Другое описание", "category": "report", "status": "in_progress"},
    )
    # после «Сохранить» пользователь оказывается в списке всех заявок
    assert r.url.path == "/tickets" and "Мои заявки" in r.text
    assert "сохранены" in r.text and "Новое название" in r.text
    assert '<option value="in_progress" selected>' in r.text
    # данные действительно сохранились: страница редактирования показывает новые значения и цвет статуса
    page = client.get(f"/tickets/{tid}").text
    assert 'class="status-select status-in_progress"' in page and '<option value="report" selected>' in page

    r = client.post(f"/tickets/{tid}", data={"title": "", "description": "x", "category": "bot", "status": "new"})
    assert r.status_code == 422 and "Введите название" in r.text


def test_quick_status_change_from_list(client):
    register(client)
    create_ticket(client, title="Смена статуса")
    tid = ticket_id_from_list(client.get("/tickets").text)
    r = client.post(f"/tickets/{tid}/status", data={"status": "in_progress"})
    assert "изменён на «В работе»" in r.text
    r = client.post(f"/tickets/{tid}/status", data={"status": "bogus"})
    assert "Статус не изменён" in r.text


# --- Доступ: чужие заявки недоступны ---------------------------------------------------

def test_user_cannot_see_or_change_foreign_tickets(client, fake):
    register(client, "a@example.com")
    create_ticket(client, title="Секретная заявка A")
    tid = ticket_id_from_list(client.get("/tickets").text)
    client.post("/logout")

    register(client, "b@example.com")
    assert "Секретная заявка A" not in client.get("/tickets").text
    assert "Заявок пока нет" in client.get("/tickets").text

    r = client.get(f"/tickets/{tid}")
    assert "не найдена или у вас нет к ней доступа" in r.text

    r = client.post(f"/tickets/{tid}", data={"title": "Взлом", "description": "x", "category": "bot", "status": "done"})
    assert "не найдена" in r.text
    r = client.post(f"/tickets/{tid}/status", data={"status": "done"})
    assert "не найдена" in r.text
    assert fake.tickets[tid]["title"] == "Секретная заявка A" and fake.tickets[tid]["status"] == "new"

    # попытка занять чужой id при создании не раскрывает и не перезаписывает чужую заявку
    r = create_ticket(client, title="Подмена", client_id=tid)
    assert r.status_code in (400, 503) and "не сохранена" in r.text
    assert fake.tickets[tid]["title"] == "Секретная заявка A"


# --- Сессия ----------------------------------------------------------------------------

def test_expired_access_token_is_refreshed_silently(client, fake):
    register(client)
    create_ticket(client, title="После refresh")
    user = fake.users["a@example.com"]
    domain = next(c.domain for c in client.cookies.jar if c.name == "sb_access")
    expired = make_jwt(user["id"], user["email"], ttl=-100)
    client.cookies.delete("sb_access")
    client.cookies.set("sb_access", expired, domain=domain, path="/")

    r = client.get("/tickets")
    assert r.status_code == 200 and "После refresh" in r.text and fake.refresh_calls == 1
    assert client.cookies.get("sb_access") != expired  # cookie заменена свежим токеном


def test_broken_session_redirects_to_login(client):
    register(client)
    client.cookies.set("sb_access", "garbage")
    client.cookies.set("sb_refresh", "unknown-refresh-token")
    r = client.get("/tickets", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_unknown_ticket_id_is_handled(client):
    register(client)
    assert "не найдена" in client.get("/tickets/not-a-uuid").text
    assert "не найдена" in client.get("/tickets/00000000-0000-0000-0000-000000000000").text
