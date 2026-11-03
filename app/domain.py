from __future__ import annotations

import uuid

# Код (хранится в базе) -> подпись в интерфейсе. Порядок важен: так показываем в списках.
CATEGORIES: dict[str, str] = {
    "bot": "Бот",
    "integration": "Интеграция",
    "report": "Отчёт",
    "other": "Другое",
}
STATUSES: dict[str, str] = {
    "new": "Новая",
    "in_progress": "В работе",
    "done": "Завершена",
}

TITLE_MAX = 200
DESCRIPTION_MAX = 5000
SEARCH_MAX = 100


def validate_ticket(
    title: str | None,
    description: str | None,
    category: str | None,
    status: str | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Возвращает (очищенные значения, ошибки по полям). Все поля обязательны."""
    cleaned = {
        "title": (title or "").strip(),
        "description": (description or "").strip(),
        "category": (category or "").strip(),
    }
    errors: dict[str, str] = {}

    if not cleaned["title"]:
        errors["title"] = "Введите название заявки."
    elif len(cleaned["title"]) > TITLE_MAX:
        errors["title"] = f"Название не должно быть длиннее {TITLE_MAX} символов."

    if not cleaned["description"]:
        errors["description"] = "Опишите задачу."
    elif len(cleaned["description"]) > DESCRIPTION_MAX:
        errors["description"] = f"Описание не должно быть длиннее {DESCRIPTION_MAX} символов."

    if not cleaned["category"]:
        errors["category"] = "Выберите категорию."
    elif cleaned["category"] not in CATEGORIES:
        errors["category"] = "Выберите категорию из списка."

    if status is not None:
        cleaned["status"] = (status or "").strip()
        if cleaned["status"] not in STATUSES:
            errors["status"] = "Выберите статус из списка."

    return cleaned, errors


def parse_uuid(raw: object) -> str | None:
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, TypeError, AttributeError):
        return None
