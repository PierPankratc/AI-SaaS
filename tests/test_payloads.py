"""Тесты payload кнопок и валидации пользовательского ввода."""

from src.bot.payloads import Action, decode_payload, encode_payload
from src.bot.validation import sanitize_teacher_query


def test_payload_roundtrip() -> None:
    raw = encode_payload(Action.GROUP, value=8948)
    decoded = decode_payload(raw)

    assert decoded is not None
    assert decoded.action is Action.GROUP
    assert decoded.value == 8948


def test_decode_accepts_dict() -> None:
    decoded = decode_payload({"action": "level", "value": "bachelor"})

    assert decoded is not None
    assert decoded.action is Action.LEVEL
    assert decoded.value == "bachelor"


def test_decode_rejects_garbage() -> None:
    assert decode_payload(None) is None
    assert decode_payload("not-json") is None
    assert decode_payload({"action": "hack"}) is None
    assert decode_payload({"action": "group", "evil": 1}) is None


def test_decode_rejects_oversized() -> None:
    assert decode_payload({"action": "menu", "value": "x" * 500}) is None
    assert decode_payload("x" * 300) is None


def test_page_validation() -> None:
    assert decode_payload({"action": "page", "value": "faculties", "page": -1}) is None

    decoded = decode_payload({"action": "page", "value": "faculties", "page": 2})
    assert decoded is not None
    assert decoded.page == 2


def test_sanitize_teacher_query() -> None:
    assert sanitize_teacher_query("  Адеева   Татьяна ") == "Адеева Татьяна"
    assert sanitize_teacher_query("Иванов-Петров") == "Иванов-Петров"
    assert sanitize_teacher_query("'; DROP TABLE users; --") is None
    assert sanitize_teacher_query("А") is None
    assert sanitize_teacher_query("") is None
    assert sanitize_teacher_query("Адеева1") is None
