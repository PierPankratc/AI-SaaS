"""Тесты сервиса расписания: SQLite, догрузка из ЭИОС и форматирование."""

from datetime import date

import pytest_asyncio

from src.api.schemas import Schedule
from src.db.schedule_repository import ScheduleRepository, SemesterKey
from src.db.sqlite_db import SqliteDatabase
from src.services.schedule_service import (
    Role,
    ScheduleService,
    format_day,
    format_week,
    lessons_on,
    split_message,
    week_monday,
)


def _lesson(
    code: int,
    day_iso: str,
    start: str,
    end: str,
    subject: str,
    room: str,
    teacher: str,
    group: str,
    subgroup: int = 0,
    replacement: bool = False,
) -> dict:
    return {
        "код": code,
        "дата": f"{day_iso}T00:00:00",
        "датаНачала": f"{day_iso}T{start}:00",
        "датаОкончания": f"{day_iso}T{end}:00",
        "начало": start,
        "конец": end,
        "деньНедели": 1,
        "день_недели": "Понедельник",
        "дисциплина": subject,
        "аудитория": room,
        "фиоПреподавателя": teacher,
        "группа": group,
        "номерПодгруппы": subgroup,
        "замена": replacement,
    }


def make_schedule() -> Schedule:
    return Schedule.model_validate(
        {
            "isCyclicalSchedule": False,
            "rasp": [
                _lesson(
                    1, "2026-11-09", "10:10", "11:40",
                    "лек Математический анализ", "Б-310", "Землякова И. В.", "26-ПМбо-1",
                ),
                _lesson(
                    2, "2026-11-10", "08:30", "10:00",
                    "лаб Web-программирование", "Б-304", "Волков А. А.", "26-ПМбо-1",
                    subgroup=1,
                ),
                _lesson(
                    3, "2026-11-10", "08:30", "10:00",
                    "лаб Web-программирование", "Б-305", "Пигузов А. А.", "26-ПМбо-1",
                    subgroup=2,
                ),
                _lesson(
                    4, "2026-11-10", "10:10", "11:40",
                    "пр История", "А-101", "Ганжа Н. В.", "26-ПМбо-1",
                    replacement=True,
                ),
            ],
            "info": {
                "group": {"name": "26-ПМбо-1", "groupID": 8948, "year": "2026-2027"},
                "prepod": {"name": "", "userID": None},
                "curSem": 1,
            },
        }
    )


@pytest_asyncio.fixture
async def repository(tmp_path):
    database = SqliteDatabase(str(tmp_path / "schedule.db"))
    await database.create_schema()
    try:
        yield ScheduleRepository(database.session_factory)
    finally:
        await database.dispose()


async def store_week(
    repository: ScheduleRepository,
    *,
    target_id: int = 8948,
    covered_from: date = date(2026, 11, 9),
    covered_to: date = date(2026, 11, 15),
) -> None:
    schedule = make_schedule()
    await repository.replace_target_schedule(
        role=Role.STUDENT.value,
        target_id=target_id,
        target_name="26-ПМбо-1",
        semester=SemesterKey("2026-2027", 1),
        lessons=schedule.lessons,
        covered_from=covered_from,
        covered_to=covered_to,
    )


def test_week_monday() -> None:
    assert week_monday(date(2026, 11, 9)) == date(2026, 11, 9)
    assert week_monday(date(2026, 11, 10)) == date(2026, 11, 9)
    assert week_monday(date(2026, 11, 11)) == date(2026, 11, 9)


def test_lessons_on_filters_and_sorts() -> None:
    schedule = make_schedule()
    tuesday = lessons_on(schedule.lessons, date(2026, 11, 10))
    assert [lesson.subgroup for lesson in tuesday] == [1, 2, 0]
    assert [
        lesson.id for lesson in lessons_on(schedule.lessons, date(2026, 11, 9))
    ] == [1]


def test_format_day_student() -> None:
    tuesday = lessons_on(make_schedule().lessons, date(2026, 11, 10))
    text = format_day(
        tuesday, date(2026, 11, 10), Role.STUDENT, "Группа 26-ПМбо-1"
    )

    assert "Вторник" in text
    assert "п/г 1" in text and "п/г 2" in text
    assert "Б-304" in text
    assert "Волков А. А." in text
    assert "замена" in text


def test_format_day_teacher_shows_group() -> None:
    tuesday = lessons_on(make_schedule().lessons, date(2026, 11, 10))
    text = format_day(tuesday, date(2026, 11, 10), Role.TEACHER, "Адеева Т. Н.")
    assert "гр. 26-ПМбо-1" in text


def test_format_day_empty() -> None:
    text = format_day([], date(2026, 11, 9), Role.STUDENT)
    assert "Занятий нет" in text


def test_format_week_groups_by_day() -> None:
    text = format_week(make_schedule(), Role.STUDENT, "Группа 26-ПМбо-1")
    assert "Понедельник" in text and "Вторник" in text
    assert "Математический анализ" in text
    assert "Web-программирование" in text


def test_split_message_respects_limit() -> None:
    text = "\n".join(["x" * 60 for _ in range(10)])
    parts = split_message(text, limit=130)
    assert len(parts) > 1
    assert all(len(part) <= 130 for part in parts)
    assert "\n".join(parts) == text


async def test_week_reads_from_sqlite(repository) -> None:
    await store_week(repository)
    service = ScheduleService(repository)

    schedule = await service.get_week(Role.STUDENT, 8948, date(2026, 11, 10))

    assert len(schedule.lessons) == 4
    assert schedule.info is not None
    assert schedule.info.group is not None
    assert schedule.info.group.name == "26-ПМбо-1"


async def test_empty_database_returns_no_lessons(repository) -> None:
    service = ScheduleService(repository)

    text = await service.day_text(Role.STUDENT, 8948, date(2026, 11, 9))

    assert "Занятий нет" in text


async def test_day_and_week_text(repository) -> None:
    await store_week(repository)
    service = ScheduleService(repository)

    day_text = await service.day_text(
        Role.STUDENT, 8948, date(2026, 11, 10), title="Группа 26-ПМбо-1"
    )
    assert "Вторник" in day_text

    week_text = await service.week_text(
        Role.STUDENT, 8948, date(2026, 11, 9)
    )
    assert "Понедельник" in week_text and "Вторник" in week_text

