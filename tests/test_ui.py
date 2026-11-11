"""Тесты спецификаций клавиатур: payload, пагинация и лимиты ВК."""

from src.api.schemas import EducationLevel, Faculty, Group, Teacher
from src.bot.payloads import Action, decode_payload
from src.bot.ui import (
    MAX_BUTTONS_PER_ROW,
    MAX_ROWS,
    courses_keyboard,
    faculties_keyboard,
    groups_keyboard,
    levels_keyboard,
    main_menu_keyboard,
    paginate,
    role_keyboard,
    teachers_keyboard,
)


def _action(button) -> Action | None:
    payload = decode_payload(button.payload)
    return payload.action if payload else None


def test_role_keyboard_payloads() -> None:
    keyboard = role_keyboard()
    payloads = [decode_payload(button.payload) for button in keyboard.buttons()]

    assert all(payload is not None for payload in payloads)
    assert {payload.value for payload in payloads if payload} == {"student", "teacher"}
    assert all(_action(button) is Action.ROLE for button in keyboard.buttons())


def test_paginate_clamps() -> None:
    items = list(range(20))

    page_items, page, pages = paginate(items, 0)
    assert (page, pages, page_items) == (0, 3, list(range(8)))

    page_items, page, pages = paginate(items, 5)
    assert (page, pages) == (2, 3)
    assert page_items == list(range(16, 20))

    _, page, _ = paginate(items, -10)
    assert page == 0


def test_faculties_pagination_and_limits() -> None:
    faculties = [Faculty(id=index, name=f"И{index}") for index in range(1, 21)]
    keyboard = faculties_keyboard(faculties, page=0)

    assert len(keyboard.rows) <= MAX_ROWS
    assert all(len(row) <= MAX_BUTTONS_PER_ROW for row in keyboard.rows)

    nav = [button for button in keyboard.buttons() if _action(button) is Action.PAGE]
    assert len(nav) == 1  # на первой странице только «➡️»


def test_groups_and_teachers_keyboards() -> None:
    groups = [
        Group(
            id=index,
            name=f"26-XXбо-{index}",
            course=1,
            faculty_id=36,
            faculty_name="ИВИТШ",
            education_level=EducationLevel.BACHELOR,
        )
        for index in range(1, 10)
    ]
    keyboard = groups_keyboard(groups)
    assert len([b for b in keyboard.buttons() if _action(b) is Action.GROUP]) == 8

    teachers = [Teacher(id=index, name=f"Иванов Иван {index}") for index in range(1, 3)]
    teacher_keyboard = teachers_keyboard(teachers)
    assert any(_action(b) is Action.TEACHER for b in teacher_keyboard.buttons())


def test_levels_courses_menu() -> None:
    levels = levels_keyboard([EducationLevel.BACHELOR, EducationLevel.MAGISTRACY])
    assert [button.text for button in levels.buttons()] == ["Бакалавриат", "Магистратура"]

    courses = courses_keyboard([1, 2, 3])
    assert [button.text for button in courses.buttons()] == ["1 курс", "2 курс", "3 курс"]

    menu = main_menu_keyboard()
    actions = {_action(button) for button in menu.buttons()}
    assert actions == {Action.TODAY, Action.TOMORROW, Action.WEEK, Action.CHANGE}
