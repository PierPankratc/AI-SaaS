"""Формат payload кнопок ВКонтакте: строгий разбор и валидация.

Payload содержит только ASCII-действие и небольшие значения (id или код),
поэтому он гарантированно укладывается в лимит ВК (255 байт). Разбор
запрещает лишние поля и проверяет диапазоны — это защищает роутинг от
подделанных данных.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MAX_PAYLOAD_BYTES = 255
MAX_VALUE_STR = 64


class Action(str, Enum):
    """Действия кнопок и переходы сценария."""

    ROLE = "role"
    FACULTY = "faculty"
    LEVEL = "level"
    COURSE = "course"
    GROUP = "group"
    TEACHER = "teacher"
    PAGE = "page"
    TODAY = "today"
    TOMORROW = "tomorrow"
    WEEK = "week"
    CHANGE = "change"
    MENU = "menu"


class Payload(BaseModel):
    """Разобранный и провалидированный payload."""

    model_config = ConfigDict(extra="forbid")

    action: Action
    value: str | int | None = None
    page: int | None = Field(default=None, ge=0, le=1000)

    @field_validator("value")
    @classmethod
    def _cap_value(cls, value: str | int | None) -> str | int | None:
        if isinstance(value, str) and len(value) > MAX_VALUE_STR:
            raise ValueError("payload value слишком длинное")
        return value


def make_payload(
    action: Action | str,
    *,
    value: str | int | None = None,
    page: int | None = None,
) -> dict[str, Any]:
    """Собирает компактный payload для кнопки."""
    data: dict[str, Any] = {"action": Action(action).value}
    if value is not None:
        data["value"] = value
    if page is not None:
        data["page"] = int(page)
    return data


def encode_payload(
    action: Action | str,
    *,
    value: str | int | None = None,
    page: int | None = None,
) -> str:
    """Сериализует payload в JSON-строку (для проверки размера/тестов)."""
    return json.dumps(
        make_payload(action, value=value, page=page), separators=(",", ":")
    )


def decode_payload(raw: Any) -> Payload | None:
    """Разбирает payload из события ВК; возвращает ``None`` при ошибке."""
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "ignore")
    if isinstance(raw, str):
        if not raw or len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            return None
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw, dict):
        return None
    try:
        return Payload.model_validate(raw)
    except ValidationError:
        return None
