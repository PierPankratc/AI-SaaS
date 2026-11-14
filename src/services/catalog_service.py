"""Сервис каталога ЭИОС: иерархия институтов/ступеней/курсов/групп и
поиск преподавателей по локальной базе данных.

Справочники хранятся в SQLite (``groups``, ``teachers``, ``academic_years``)
и заполняются CLI-синхронизацией. Бот читает их без сетевых запросов.
"""

from __future__ import annotations

from typing import Any, Iterable

from src.api.schemas import (
    LEVEL_ORDER,
    EducationLevel,
    Faculty,
    Group,
    Teacher,
)


def normalize_person_name(text: str) -> str:
    """Нормализует ФИО для поиска: регистр, «ё»→«е», лишние пробелы."""
    return " ".join(text.lower().replace("ё", "е").split())


def build_faculties(groups: Iterable[Group]) -> list[Faculty]:
    """Собирает уникальные институты из групп (по имени)."""
    seen: dict[int, str] = {}
    for group in groups:
        seen.setdefault(group.faculty_id, group.faculty_name)
    return sorted(
        (Faculty(id=faculty_id, name=name) for faculty_id, name in seen.items()),
        key=lambda faculty: faculty.name,
    )


def available_levels(groups: Iterable[Group], faculty_id: int) -> list[EducationLevel]:
    """Ступени обучения, реально присутствующие у института (в порядке меню)."""
    present = {
        group.education_level
        for group in groups
        if group.faculty_id == faculty_id
    }
    return [level for level in LEVEL_ORDER if level in present]


def available_courses(
    groups: Iterable[Group],
    faculty_id: int,
    level: EducationLevel,
) -> list[int]:
    """Номера курсов для института и ступени (по возрастанию)."""
    return sorted(
        {
            group.course
            for group in groups
            if group.faculty_id == faculty_id and group.education_level == level
        }
    )


def select_groups(
    groups: Iterable[Group],
    faculty_id: int,
    level: EducationLevel,
    course: int,
) -> list[Group]:
    """Группы института/ступени/курса (по алфавиту)."""
    return sorted(
        (
            group
            for group in groups
            if group.faculty_id == faculty_id
            and group.education_level == level
            and group.course == course
        ),
        key=lambda group: group.name,
    )


def search_teachers(
    teachers: Iterable[Teacher],
    query: str,
    limit: int | None = None,
) -> list[Teacher]:
    """Ищет преподавателей по фамилии или ФИО (регистр и «ё» не важны).

    Для однословного запроса сравнивается только фамилия (первое слово),
    чтобы «Петров» не совпадал с отчеством «Петрович». Для нескольких слов
    все токены должны встречаться в полном ФИО. Совпадение по фамилии
    целиком ранжируется выше, чем по её началу или части.
    """
    normalized_query = normalize_person_name(query)
    if not normalized_query:
        return []

    tokens = normalized_query.split()
    scored: list[tuple[int, str, Teacher]] = []

    for teacher in teachers:
        full_name = normalize_person_name(teacher.name)
        parts = full_name.split()
        surname = parts[0] if parts else ""

        if len(tokens) == 1:
            token = tokens[0]
            if token not in surname:
                continue
            if surname == token:
                score = 0
            elif surname.startswith(token):
                score = 1
            else:
                score = 2
        else:
            if not all(token in full_name for token in tokens):
                continue
            score = 3

        scored.append((score, teacher.name, teacher))

    scored.sort(key=lambda item: (item[0], item[1]))
    ordered = [teacher for _, _, teacher in scored]
    return ordered[:limit] if limit else ordered


class CatalogService:
    """Справочники ЭИОС, сохранённые в локальной SQLite.

    Сетевых запросов нет: данные готовит CLI-синхронизация
    (``python -m src.tools.sync_schedule``).
    """

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def get_years(self) -> list[str]:
        return await self._repository.get_years()

    async def get_current_year(self) -> str | None:
        """Последний (актуальный) учебный год."""
        years = await self.get_years()
        return years[-1] if years else None

    async def get_groups(self, year: str | None = None) -> list[Group]:
        return await self._repository.get_groups(year)

    async def get_teachers(self) -> list[Teacher]:
        return await self._repository.get_teachers()



    # --- иерархия для сценария регистрации ---

    async def get_faculties(self, year: str | None = None) -> list[Faculty]:
        return build_faculties(await self.get_groups(year))

    async def get_levels(
        self,
        faculty_id: int,
        year: str | None = None,
    ) -> list[EducationLevel]:
        return available_levels(await self.get_groups(year), faculty_id)

    async def get_courses(
        self,
        faculty_id: int,
        level: EducationLevel,
        year: str | None = None,
    ) -> list[int]:
        return available_courses(
            await self.get_groups(year), faculty_id, level
        )

    async def get_groups_for(
        self,
        faculty_id: int,
        level: EducationLevel,
        course: int,
        year: str | None = None,
    ) -> list[Group]:
        return select_groups(
            await self.get_groups(year), faculty_id, level, course
        )

    async def search_teachers(
        self,
        query: str,
        limit: int | None = None,
    ) -> list[Teacher]:
        return search_teachers(await self.get_teachers(), query, limit)
