"""Тесты локального репозитория расписания (SQLite, без ЭИОС)."""

from datetime import date

import pytest_asyncio

from src.api.schemas import EducationLevel, Group, Lesson, Teacher
from src.db.schedule_repository import ScheduleRepository, SemesterKey
from src.db.sqlite_db import SqliteDatabase


def lesson(
    code: int,
    day_iso: str,
    start: str,
    end: str,
    subject: str,
    *,
    subgroup: int = 0,
    group: str = "26-ПМбо-1",
) -> Lesson:
    return Lesson.model_validate(
        {
            "код": code,
            "дата": f"{day_iso}T00:00:00",
            "датаНачала": f"{day_iso}T{start}:00",
            "датаОкончания": f"{day_iso}T{end}:00",
            "начало": start,
            "конец": end,
            "деньНедели": 1,
            "день_недели": "Понедельник",
            "дисциплина": subject,
            "аудитория": "Б-310",
            "фиоПреподавателя": "Землякова И. В.",
            "группа": group,
            "номерПодгруппы": subgroup,
        }
    )


def lessons() -> list[Lesson]:
    return [
        lesson(1, "2026-11-09", "10:10", "11:40", "лек Математический анализ"),
        lesson(2, "2026-11-10", "08:30", "10:00", "лаб Web", subgroup=2),
        lesson(3, "2026-11-10", "08:30", "10:00", "лаб Web", subgroup=1),
        lesson(4, "2026-11-12", "10:10", "11:40", "пр История"),
    ]


@pytest_asyncio.fixture
async def repository(tmp_path):
    database = SqliteDatabase(str(tmp_path / "schedule.db"))
    await database.create_schema()
    try:
        yield ScheduleRepository(database.session_factory)
    finally:
        await database.dispose()


async def store(repository: ScheduleRepository, *items: Lesson) -> int:
    return await repository.replace_target_schedule(
        role="student",
        target_id=8948,
        target_name="26-ПМбо-1",
        semester=SemesterKey("2026-2027", 1),
        lessons=items or lessons(),
        covered_from=date(2026, 11, 9),
        covered_to=date(2026, 11, 15),
    )


async def test_replace_and_read_week(repository) -> None:
    await store(repository)

    result = await repository.get_lessons(
        "student", 8948, date(2026, 11, 9), date(2026, 11, 15)
    )

    assert len(result) == 4
    assert [item.subject for item in result] == [
        "лек Математический анализ",
        "лаб Web",
        "лаб Web",
        "пр История",
    ]
    assert [item.subgroup for item in result[1:3]] == [1, 2]
    assert result[0].lesson_date.date() == date(2026, 11, 9)


async def test_resync_replaces_without_duplicates(repository) -> None:
    await store(repository)
    await store(repository, lessons()[0])

    stats = await repository.stats()
    assert stats.lessons == 1
    assert stats.targets == 1
    assert stats.semesters == 1


async def test_isolation_between_roles_and_targets(repository) -> None:
    item = lessons()[0]
    await store(repository, item)
    await repository.replace_target_schedule(
        role="student",
        target_id=1,
        target_name="Другая группа",
        semester=SemesterKey("2026-2027", 1),
        lessons=[item],
        covered_from=date(2026, 11, 9),
        covered_to=date(2026, 11, 15),
    )
    await repository.replace_target_schedule(
        role="teacher",
        target_id=14,
        target_name="Адеева Т. Н.",
        semester=SemesterKey("2026-2027", 1),
        lessons=[item],
        covered_from=date(2026, 11, 9),
        covered_to=date(2026, 11, 15),
    )

    assert len(
        await repository.get_lessons(
            "student", 8948, date(2026, 11, 9), date(2026, 11, 15)
        )
    ) == 1
    assert len(
        await repository.get_lessons(
            "student", 1, date(2026, 11, 9), date(2026, 11, 15)
        )
    ) == 1
    assert await repository.get_lessons(
        "student", 14, date(2026, 11, 9), date(2026, 11, 15)
    ) == []
    assert len(
        await repository.get_lessons(
            "teacher", 14, date(2026, 11, 9), date(2026, 11, 15)
        )
    ) == 1


async def test_week_store_and_coverage(repository) -> None:
    monday = date(2026, 11, 9)
    assert not await repository.covers("student", 8948, monday)

    await repository.replace_target_week(
        role="student",
        target_id=8948,
        target_name="26-ПМбо-1",
        semester=SemesterKey("2026-2027", 1),
        monday=monday,
        lessons=lessons(),
    )

    assert await repository.covers("student", 8948, monday)
    assert await repository.covers("student", 8948, date(2026, 11, 15))
    assert not await repository.covers("student", 8948, date(2026, 11, 16))
    assert not await repository.was_synced("student", 8948, 3_600)
    assert (
        len(
            await repository.get_lessons(
                "student", 8948, monday, date(2026, 11, 15)
            )
        )
        == 4
    )


async def test_week_store_replaces_only_its_dates(repository) -> None:
    await repository.replace_target_week(
        role="student",
        target_id=8948,
        target_name="26-ПМбо-1",
        semester=SemesterKey("2026-2027", 1),
        monday=date(2026, 11, 2),
        lessons=[
            lesson(10, "2026-11-03", "08:30", "10:00", "лек Физика"),
        ],
    )
    await repository.replace_target_week(
        role="student",
        target_id=8948,
        target_name="26-ПМбо-1",
        semester=SemesterKey("2026-2027", 1),
        monday=date(2026, 11, 9),
        lessons=lessons(),
    )

    first_week = await repository.get_lessons(
        "student", 8948, date(2026, 11, 2), date(2026, 11, 8)
    )
    assert [item.subject for item in first_week] == ["лек Физика"]
    assert await repository.covers("student", 8948, date(2026, 11, 3))
    assert await repository.covers("student", 8948, date(2026, 11, 10))


async def test_target_name_and_list_targets(repository) -> None:
    await store(repository)

    assert await repository.get_target_name("student", 8948) == "26-ПМбо-1"
    assert await repository.get_target_name("student", 1) is None

    rows = await repository.list_targets()
    assert len(rows) == 1
    assert rows[0]["role"] == "student"
    assert rows[0]["target_id"] == 8948
    assert rows[0]["academic_year"] == "2026-2027"
    assert rows[0]["semester"] == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["source"] == "semester"
    assert rows[0]["lessons_count"] == 4
    assert rows[0]["covered_from"] == date(2026, 11, 9)


async def test_catalog_roundtrip(repository) -> None:
    groups = [
        Group(
            id=8948,
            name="26-ПМбо-1",
            course=1,
            faculty_id=36,
            faculty_name="ИВИТШ",
            year="2026-2027",
            education_level=EducationLevel.BACHELOR,
            program_code="ПМ",
        )
    ]
    teachers = [Teacher(id=14, name="Адеева Татьяна Николаевна")]

    await repository.save_catalog(
        years=["2025-2026", "2026-2027"],
        groups=groups,
        teachers=teachers,
        year="2026-2027",
    )

    assert await repository.get_years() == ["2025-2026", "2026-2027"]
    loaded_groups = await repository.get_groups("2026-2027")
    assert len(loaded_groups) == 1
    assert loaded_groups[0].name == "26-ПМбо-1"
    assert loaded_groups[0].education_level is EducationLevel.BACHELOR
    assert [teacher.id for teacher in await repository.get_teachers()] == [14]

    stats = await repository.stats()
    assert stats.groups == 1
    assert stats.teachers == 1
    assert stats.lessons == 0
    assert stats.last_catalog_sync is not None


async def test_teacher_ids_with_lessons(repository) -> None:
    item = lessons()[0].model_copy(update={"teacher_id": 14})
    await store(repository, item)

    assert await repository.teacher_ids_with_lessons() == [14]


async def test_rebuild_teacher_schedules_from_groups(repository) -> None:
    await repository.save_teachers([Teacher(id=14, name="Адеева Т. Н.")])
    first = lessons()[0].model_copy(
        update={"teacher_id": 14, "group_name": "26-ПМбо-1"}
    )
    second = first.model_copy(update={"group_name": "26-ПМбо-2"})
    await store(repository, first, second)

    total = await repository.rebuild_teacher_schedules()

    assert total == 1
    teacher_lessons = await repository.get_lessons(
        "teacher", 14, date(2026, 11, 9), date(2026, 11, 15)
    )
    assert len(teacher_lessons) == 1
    assert teacher_lessons[0].group_name is not None
    assert "26-ПМбо-1" in teacher_lessons[0].group_name
    assert "26-ПМбо-2" in teacher_lessons[0].group_name
    assert await repository.get_target_name("teacher", 14) == "Адеева Т. Н."
    assert await repository.was_synced("teacher", 14, 3_600)

    # Занятия групп не пострадали.
    assert (
        len(
            await repository.get_lessons(
                "student", 8948, date(2026, 11, 9), date(2026, 11, 15)
            )
        )
        == 2
    )


async def test_was_synced_respects_interval(repository) -> None:
    await store(repository)

    assert await repository.was_synced("student", 8948, 3_600)
    assert not await repository.was_synced("student", 1, 3_600)


async def test_empty_sync_state_is_recorded(repository) -> None:
    await repository.record_sync_empty(
        role="student",
        target_id=8948,
        target_name="26-ПМбо-1",
        semester=SemesterKey("2026-2027", 1),
    )

    assert await repository.was_synced("student", 8948, 3_600)
    assert await repository.get_target_name("student", 8948) == "26-ПМбо-1"
    assert not await repository.covers("student", 8948, date(2026, 11, 9))

    rows = await repository.list_targets()
    assert rows[0]["status"] == "empty"
    assert rows[0]["source"] == "semester"
    assert rows[0]["lessons_count"] == 0


async def test_empty_after_error_becomes_synced(repository) -> None:
    await repository.record_sync_error(
        role="student",
        target_id=8948,
        target_name="26-ПМбо-1",
        semester=SemesterKey("2026-2027", 1),
        error="429",
    )
    await repository.record_sync_empty(
        role="student",
        target_id=8948,
        target_name="26-ПМбо-1",
        semester=SemesterKey("2026-2027", 1),
    )

    assert await repository.was_synced("student", 8948, 3_600)
    rows = await repository.list_targets()
    assert rows[0]["status"] == "empty"
    assert rows[0]["source"] == "semester"

