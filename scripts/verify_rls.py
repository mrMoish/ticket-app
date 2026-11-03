"""Проверка Row Level Security на живом проекте Supabase — запросами напрямую к REST API, мимо приложения.

Запуск (из корня проекта, .env заполнен):  python scripts/verify_rls.py
Нужно, чтобы в Supabase было выключено подтверждение почты (Authentication → Providers → Email → Confirm email),
иначе тестовые пользователи не смогут войти сразу после регистрации.
Скрипт создаёт двух тестовых пользователей и одну заявку. Удалить их можно в Dashboard → Authentication → Users
(заявки удалятся каскадом).
"""

from __future__ import annotations

import os
import sys
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()
URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_ANON_KEY", "")
DOMAIN = os.environ.get("VERIFY_EMAIL_DOMAIN", "example.com")

if not URL or not KEY:
    sys.exit("Задайте SUPABASE_URL и SUPABASE_ANON_KEY в .env")

http = httpx.Client(base_url=URL, timeout=15)
results: list[tuple[bool, str]] = []


def check(ok: bool, text: str) -> None:
    results.append((ok, text))
    print(("PASS  " if ok else "FAIL  ") + text)


def headers(token: str | None = None, **extra: str) -> dict[str, str]:
    h = {"apikey": KEY, **extra}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def make_user(label: str) -> tuple[str, str]:
    email = f"rls-{label}-{uuid.uuid4().hex[:8]}@{DOMAIN}"
    password = uuid.uuid4().hex
    r = http.post("/auth/v1/signup", headers=headers(), json={"email": email, "password": password})
    data = r.json()
    if r.status_code >= 400 or "access_token" not in data:
        sys.exit(f"Не удалось создать пользователя ({r.status_code}): {r.text[:300]}\n"
                 "Выключите подтверждение почты или задайте VERIFY_EMAIL_DOMAIN с допустимым доменом.")
    return data["access_token"], data["user"]["id"]


token_a, id_a = make_user("a")
token_b, id_b = make_user("b")
rep = {"Prefer": "return=representation"}
ticket = {"id": str(uuid.uuid4()), "title": "RLS-проверка", "description": "тест", "category": "bot"}

r = http.post("/rest/v1/tickets", headers=headers(token_a, **rep), json=ticket)
check(r.status_code == 201 and r.json()[0]["user_id"] == id_a and r.json()[0]["status"] == "new",
      "A создаёт заявку: user_id = A, статус «new» проставлен базой")

r = http.get("/rest/v1/tickets", headers=headers(token_a))
check(r.status_code == 200 and [t["id"] for t in r.json()] == [ticket["id"]], "A видит свою заявку")

r = http.get("/rest/v1/tickets", headers=headers())
check(r.status_code in (401, 403) or r.json() == [], "аноним (без входа) не получает заявки")

r = http.get("/rest/v1/tickets", headers=headers(token_b))
check(r.status_code == 200 and r.json() == [], "B не видит заявок A в общем списке")

r = http.get("/rest/v1/tickets", headers=headers(token_b), params={"id": f"eq.{ticket['id']}"})
check(r.status_code == 200 and r.json() == [], "B не получает заявку A даже по точному id")

r = http.patch("/rest/v1/tickets", headers=headers(token_b, **rep), params={"id": f"eq.{ticket['id']}"},
               json={"title": "ВЗЛОМ", "status": "done"})
check(r.status_code in (200, 204) and not r.json(), "B не может изменить заявку A (0 строк затронуто)")

r = http.post("/rest/v1/tickets", headers=headers(token_b),
              json={"id": str(uuid.uuid4()), "user_id": id_a, "title": "x", "description": "x", "category": "bot"})
check(r.status_code in (401, 403), "B не может создать заявку от имени A")

r = http.post("/rest/v1/tickets", headers=headers(token_b),
              json={"id": str(uuid.uuid4()), "title": "x", "description": "x", "category": "bot", "status": "done"})
check(r.status_code in (401, 403), "новая заявка не может стартовать с чужим статусом (только «new»)")

r = http.post("/rest/v1/tickets", headers=headers(token_a),
              json={"id": str(uuid.uuid4()), "title": "x", "description": "x", "category": "hack"})
check(r.status_code == 400, "база отклоняет недопустимую категорию")

r = http.patch("/rest/v1/tickets", headers=headers(token_a, **rep), params={"id": f"eq.{ticket['id']}"},
               json={"user_id": id_b, "created_at": "2000-01-01T00:00:00Z"})
row = r.json()[0] if r.status_code == 200 and r.json() else None
check(r.status_code in (200, 401, 403) and (row is None or (row["user_id"] == id_a and row["created_at"] > "2001")),
      "A не может передать заявку другому владельцу или подделать дату создания")

r = http.delete("/rest/v1/tickets", headers=headers(token_a), params={"id": f"eq.{ticket['id']}"})
check(r.status_code in (401, 403), "удаление заявок запрещено на уровне прав")

failed = [t for ok, t in results if not ok]
print(f"\nИтог: {len(results) - len(failed)}/{len(results)} проверок пройдено")
sys.exit(1 if failed else 0)
