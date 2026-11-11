"""Спецификации клавиатур ВКонтакте (без зависимости от ``vkbottle``).

Клавиатуры описываются структурами данных (:class:`KeyboardSpec`), а
преобразование в объекты ``vkbottle`` выполняет адаптер ``src.bot.keyboards``.
Здесь же реализована пагинация с учётом лимитов ВК (не более 10 рядов,
4 кнопок в ряду и 40 кнопок всего).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.api.schemas import EducationLevel, Faculty, Group, Teacher
from src.bot.payloads import Action, make_payload

MAX_ROWS = 10
MAX_BUTTONS_PER_ROW = 4
MAX_TOTAL_BUTTONS = 40
LIST_PAGE_SIZE = 8


@dataclass(frozen=True)
class ButtonSpec:
    """Описание кнопки: текст, payload и цвет."""

    text: str
    payload: dict
    color: str = "secondary"


@dataclass(frozen=True)
class KeyboardSpec:
    """Клавиатура как набор рядов кнопок."""

    rows: tuple[tuple[ButtonSpec, ...], ...]
    inline: bool = False

    def __post_init__(self) -> None:
        if len(self.rows) > MAX_ROWS:
            raise ValueError("Слишком много рядов кнопок")
        total = 0
        for row in self.rows:
            if not row:
                raise ValueError("Пустой ряд кнопок")
            if len(row) > MAX_BUTTONS_PER_ROW:
                raise ValueError("Слишком много кнопок в ряду")
            total += len(row)
        if total > MAX_TOTAL_BUTTONS:
            raise ValueError("Слишком много кнопок в клавиатуре")

    def buttons(self) -> list[ButtonSpec]:
        return [button for row in self.rows for button in row]


def paginate(
    items: Sequence,
    page: int,
    per_page: int = LIST_PAGE_SIZE,
) -> tuple[list, int, int]:
    """Возвращает (элементы страницы, номер страницы, всего страниц)."""
    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(page, 0), total_pages - 1)
    start = page * per_page
    return list(items[start : start + per_page]), page, total_pages


def _rows_of(
    buttons: Sequence[ButtonSpec],
    per_row: int = MAX_BUTTONS_PER_ROW,
) -> list[tuple[ButtonSpec, ...]]:
    return [
        tuple(buttons[index : index + per_row])
        for index in range(0, len(buttons), per_row)
    ]


def _page_row(
    list_name: str,
    page: int,
    total_pages: int,
) -> tuple[ButtonSpec, ...]:
    if total_pages <= 1:
        return ()
    row: list[ButtonSpec] = []
    if page > 0:
        row.append(
            ButtonSpec(
                "⬅️",
                make_payload(Action.PAGE, value=list_name, page=page - 1),
            )
        )
    if page < total_pages - 1:
        row.append(
            ButtonSpec(
                "➡️",
                make_payload(Action.PAGE, value=list_name, page=page + 1),
            )
        )
    return tuple(row)


def role_keyboard() -> KeyboardSpec:
    return KeyboardSpec(
        (
            (
                ButtonSpec(
                    "👨‍🎓 Студент",
                    make_payload(Action.ROLE, value="student"),
                    "primary",
                ),
            ),
            (
                ButtonSpec(
                    "👨‍🏫 Преподаватель",
                    make_payload(Action.ROLE, value="teacher"),
                    "primary",
                ),
            ),
        )
    )


def faculties_keyboard(
    faculties: Sequence[Faculty],
    page: int = 0,
) -> KeyboardSpec:
    page_items, page, total_pages = paginate(faculties, page)
    buttons = [
        ButtonSpec(faculty.name, make_payload(Action.FACULTY, value=faculty.id))
        for faculty in page_items
    ]
    rows = _rows_of(buttons)
    nav = _page_row("faculties", page, total_pages)
    if nav:
        rows.append(nav)
    return KeyboardSpec(tuple(rows))


def levels_keyboard(levels: Sequence[EducationLevel]) -> KeyboardSpec:
    buttons = [
        ButtonSpec(
            level.title,
            make_payload(Action.LEVEL, value=level.value),
            "primary",
        )
        for level in levels
    ]
    return KeyboardSpec(tuple(_rows_of(buttons)))


def courses_keyboard(courses: Sequence[int]) -> KeyboardSpec:
    buttons = [
        ButtonSpec(f"{course} курс", make_payload(Action.COURSE, value=course))
        for course in courses
    ]
    return KeyboardSpec(tuple(_rows_of(buttons)))


def groups_keyboard(groups: Sequence[Group], page: int = 0) -> KeyboardSpec:
    page_items, page, total_pages = paginate(groups, page)
    buttons = [
        ButtonSpec(group.name, make_payload(Action.GROUP, value=group.id))
        for group in page_items
    ]
    rows = _rows_of(buttons)
    nav = _page_row("groups", page, total_pages)
    if nav:
        rows.append(nav)
    return KeyboardSpec(tuple(rows))


def teachers_keyboard(teachers: Sequence[Teacher], page: int = 0) -> KeyboardSpec:
    page_items, page, total_pages = paginate(teachers, page)
    buttons = [
        ButtonSpec(teacher.name, make_payload(Action.TEACHER, value=teacher.id))
        for teacher in page_items
    ]
    rows = _rows_of(buttons)
    nav = _page_row("teachers", page, total_pages)
    if nav:
        rows.append(nav)
    return KeyboardSpec(tuple(rows))


def main_menu_keyboard() -> KeyboardSpec:
    return KeyboardSpec(
        (
            (
                ButtonSpec("📅 На сегодня", make_payload(Action.TODAY), "primary"),
                ButtonSpec("📆 На завтра", make_payload(Action.TOMORROW), "primary"),
            ),
            (ButtonSpec("🗓 На эту неделю", make_payload(Action.WEEK), "primary"),),
            (
                ButtonSpec(
                    "🔄 Сменить группу / профиль",
                    make_payload(Action.CHANGE),
                    "secondary",
                ),
            ),
        )
    )
