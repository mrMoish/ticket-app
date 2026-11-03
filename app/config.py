from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_anon_key: str
    cookie_secure: bool = False


@lru_cache
def get_settings() -> Settings:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not url or not key:
        raise RuntimeError(
            "Не заданы SUPABASE_URL и SUPABASE_ANON_KEY. Скопируйте .env.example в .env и заполните."
        )
    secure = os.environ.get("COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}
    return Settings(supabase_url=url, supabase_anon_key=key, cookie_secure=secure)
