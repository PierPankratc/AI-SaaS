"""Валидация пользовательского ввода: защита от инъекций и абьюза.

Ввод с клавиатуры допускается только для поиска преподавателя, поэтому
строка жёстко ограничена по длине и набору символов (буквы, пробел, дефис).
"""

from __future__ import annotations

import re

MAX_TEXT_LEN = 512
TEACHER_QUERY_MIN = 2
TEACHER_QUERY_MAX = 100

_TEACHER_QUERY_RE = re.compile(r"^[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z\- ]*$")


def clamp_text(text: str | None, limit: int = MAX_TEXT_LEN) -> str:
    """Обрезает слишком длинный ввод (защита от переполнения)."""
    if not text:
        return ""
    return text.strip()[:limit]


def sanitize_teacher_query(text: str | None) -> str | None:
    """Проверяет фамилию/ФИО для поиска преподавателя.

    Возвращает нормализованную строку или ``None``, если ввод недопустим
    (пусто, слишком коротко/длинно, есть посторонние символы).
    """
    if not text:
        return None
    cleaned = " ".join(text.split())
    if not (TEACHER_QUERY_MIN <= len(cleaned) <= TEACHER_QUERY_MAX):
        return None
    if not _TEACHER_QUERY_RE.match(cleaned):
        return None
    return cleaned
