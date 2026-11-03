"""Мини-имитация Supabase (GoTrue + PostgREST с RLS) для проверки логики приложения без сети.

Настоящий RLS проверяется скриптом scripts/verify_rls.py на живом проекте.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import unquote

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_jwt(sub: str, email: str, ttl: int = 3600) -> str:
    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'HS256'})}.{b64({'sub': sub, 'email': email, 'exp': int(time.time()) + ttl})}.sig"


class FakeSupabase:
    def __init__(self, confirm_email: bool = False):
        self.confirm_email = confirm_email
        self.users: dict[str, dict] = {}
        self.refresh_tokens: dict[str, str] = {}
        self.tickets: dict[str, dict] = {}
        self.fail_next_writes = 0  # имитация сбоя сохранения
        self.insert_attempts = 0
        self.last_insert_body: dict | None = None
        self.refresh_calls = 0

    # -- helpers --
    def _session(self, user: dict) -> dict:
        refresh = uuid.uuid4().hex
        self.refresh_tokens[refresh] = user["id"]
        return {
            "access_token": make_jwt(user["id"], user["email"]),
            "refresh_token": refresh,
            "expires_in": 3600,
            "token_type": "bearer",
            "user": {"id": user["id"], "email": user["email"]},
        }

    @staticmethod
    def _err(status: int, code: str, msg: str) -> httpx.Response:
        return httpx.Response(status, json={"error_code": code, "code": status, "msg": msg})

    def _uid(self, request: httpx.Request) -> str | None:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return None
        try:
            payload = auth[7:].split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return json.loads(base64.urlsafe_b64decode(payload))["sub"]
        except Exception:
            return None

    # -- роутинг --
    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        if path == "/auth/v1/signup":
            return self._signup(json.loads(request.content))
        if path == "/auth/v1/token":
            return self._token(params.get("grant_type"), json.loads(request.content))
        if path == "/auth/v1/logout":
            return httpx.Response(204)
        if path == "/rest/v1/tickets":
            return self._tickets(request, params)
        return httpx.Response(404, json={"message": "not found"})

    def _signup(self, body: dict) -> httpx.Response:
        email = body["email"]
        if email in self.users:
            if self.confirm_email:  # поведение GoTrue: «пустой» пользователь без identities
                return httpx.Response(200, json={"id": uuid.uuid4().hex, "email": email, "identities": []})
            return self._err(422, "user_already_exists", "User already registered")
        user = {"id": str(uuid.uuid4()), "email": email, "password": body["password"]}
        self.users[email] = user
        if self.confirm_email:
            return httpx.Response(200, json={"id": user["id"], "email": email, "identities": [{"id": "x"}]})
        return httpx.Response(200, json=self._session(user))

    def _token(self, grant: str | None, body: dict) -> httpx.Response:
        if grant == "password":
            user = self.users.get(body["email"])
            if not user or user["password"] != body["password"]:
                return self._err(400, "invalid_credentials", "Invalid login credentials")
            return httpx.Response(200, json=self._session(user))
        if grant == "refresh_token":
            self.refresh_calls += 1
            uid = self.refresh_tokens.pop(body["refresh_token"], None)
            if not uid:
                return self._err(400, "refresh_token_not_found", "Invalid Refresh Token")
            user = next(u for u in self.users.values() if u["id"] == uid)
            return httpx.Response(200, json=self._session(user))
        return self._err(400, "bad_grant", "unsupported")

    def _tickets(self, request: httpx.Request, params: dict) -> httpx.Response:
        uid = self._uid(request)
        if uid is None:
            return httpx.Response(401, json={"code": "42501", "message": "permission denied for table tickets"})

        # RLS: видны только строки владельца
        rows = [t for t in self.tickets.values() if t["user_id"] == uid]
        if "id" in params:
            rows = [t for t in rows if t["id"] == params["id"].removeprefix("eq.")]
        if "status" in params:
            rows = [t for t in rows if t["status"] == params["status"].removeprefix("eq.")]
        if "title" in params:
            needle = unquote(params["title"].removeprefix("ilike.")).replace("*", "").replace("\\", "").lower()
            rows = [t for t in rows if needle in t["title"].lower()]

        if request.method == "GET":
            rows = sorted(rows, key=lambda t: t["created_at"], reverse=True)
            return httpx.Response(200, json=[self._public(t) for t in rows])

        if self.fail_next_writes:
            self.fail_next_writes -= 1
            return httpx.Response(503, json={"message": "upstream down"})

        body = json.loads(request.content)
        if request.method == "POST":
            self.insert_attempts += 1
            self.last_insert_body = body
            if body["id"] in self.tickets:
                return httpx.Response(409, json={"code": "23505", "message": "duplicate key value"})
            row = {
                **body,
                "user_id": uid,  # default auth.uid()
                "status": "new",  # default 'new'
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.tickets[row["id"]] = row
            return httpx.Response(201, json=[self._public(row)])

        if request.method == "PATCH":
            for t in rows:
                t.update(body)
                t["updated_at"] = datetime.now(timezone.utc).isoformat()
            return httpx.Response(200, json=[self._public(t) for t in rows])

        return httpx.Response(405, json={})

    @staticmethod
    def _public(t: dict) -> dict:
        return {k: v for k, v in t.items() if k != "user_id"}


@pytest.fixture
def fake() -> FakeSupabase:
    return FakeSupabase()


@pytest.fixture
def client(fake: FakeSupabase):
    settings = Settings(supabase_url="https://fake.supabase.co", supabase_anon_key="anon-key")
    app = create_app(settings, transport=httpx.MockTransport(fake.handler))
    with TestClient(app) as c:
        yield c


def register(client: TestClient, email: str = "a@example.com", password: str = "password123"):
    return client.post("/register", data={"email": email, "password": password, "password2": password})


def new_form_id(client: TestClient) -> str:
    page = client.get("/tickets/new").text
    marker = 'name="client_id" value="'
    start = page.index(marker) + len(marker)
    return page[start : page.index('"', start)]


def create_ticket(client: TestClient, title="Бот для заказов", category="bot", description="Нужен бот", client_id=None):
    return client.post(
        "/tickets",
        data={
            "title": title,
            "description": description,
            "category": category,
            "client_id": client_id or new_form_id(client),
        },
    )
