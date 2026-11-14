"""Тесты синхронизации семестра из ЭИОС в SQLite (на фейковом клиенте)."""

from datetime import date

import pytest_asyncio

from src.api.eios_client import EiosApiError
from src.api.schemas import (
    EducationLevel,
    Group,
    Schedule,
    Teacher,
)
from src.db.schedule_repository import ScheduleRepository
from src.db.sqlite_db import SqliteDatabase
from src.services.schedule_service import Role
from src.services.sync_service import ScheduleSyncService


def _raw_lesson(code: int, day_iso: str, subject: str) -> dict:
    return {
        "код": code,
        "дата": f"{day_iso}T00:00:00",
        "датаНачала": f"{day_iso}T10:10:00",
        "датаОкончания": f"{day_iso}T11:40:00",
        "начало": "10:10",
        "конец": "11:40",
        "деньНедели": 1,
        "день_недели": "Понедельник",
        "код_Семестра": 1,
        "дисциплина": subject,
        "аудитория": "Б-310",
        "фиоПреподавателя": "Землякова И. В.",
        "группа": "26-ПМбо-1",
        "кодГруппы": 1,
        "учебныйГод": "2026-2027",
    }


def semester_schedule() -> Schedule:
    return Schedule.model_validate(
        {
            "rasp": [
                _raw_lesson(1, "2026-09-07", "лек Математический анализ"),
                _raw_lesson(2, "2026-09-14", "пр История"),
                _raw_lesson(3, "2026-11-09", "лек Физика"),
                _raw_lesson(4, "2026-12-21", "зач Математический анализ"),
            ],
            "info": {
                "group": {"name": "26-ПМбо-1", "groupID": 1, "year": "2026-2027"},
                "prepod": {"name": "Адеева Т. Н.", "userID": 14},
                "curSem": 1,
            },
        }
    )


GROUPS = [
    Group(
        id=1,
        name="26-ПМбо-1",
        course=1,
        faculty_id=36,
        faculty_name="ИВИТШ",
        year="2026-2027",
        education_level=EducationLevel.BACHELOR,
    )
]
TEACHERS = [Teacher(id=14, name="Адеева Татьяна Николаевна")]


class FakeSemesterClient:
    """Фейковый клиент ЭИОС, отдающий полный семестр одним ответом."""

    def __init__(
        self,
        schedule: Schedule | None = None,
        *,
        failing: tuple[tuple[str, int], ...] = (),
    ) -> None:
        self.schedule = schedule or semester_schedule()
        self.failing = set(failing)
        self.calls: list[tuple[str, int]] = []

    async def get_years(self) -> list[str]:
        return ["2025-2026", "2026-2027"]

    async def get_groups(self, year: str | None = None) -> list[Group]:
        return list(GROUPS)

    async def get_teachers(self) -> list[Teacher]:
        return list(TEACHERS)

    async def get_group_schedule(self, group_id: int, sdate=None) -> Schedule:
        self.calls.append(("student", group_id))
        if ("student", group_id) in self.failing:
            raise EiosApiError("unavailable")
        return self.schedule

    async def get_teacher_schedule(self, teacher_id: int, sdate=None) -> Schedule:
        self.calls.append(("teacher", teacher_id))
        if ("teacher", teacher_id) in self.failing:
            raise EiosApiError("unavailable")
        return self.schedule


@pytest_asyncio.fixture
async def repository(tmp_path):
    database = SqliteDatabase(str(tmp_path / "schedule.db"))
    await database.create_schema()
    try:
        yield ScheduleRepository(database.session_factory)
    finally:
        await database.dispose()


def service(repository: ScheduleRepository, client: FakeSemesterClient):
    return ScheduleSyncService(
        client,
        repository,
        concurrency=2,
        min_interval=3_600,
        request_delay=0,
    )


async def test_sync_catalog(repository) -> None:
    client = FakeSemesterClient()
    sync = service(repository, client)

    year = await sync.sync_catalog("2026-2027")

    assert year == "2026-2027"
    assert await repository.get_years() == ["2025-2026", "2026-2027"]
    assert [group.id for group in await repository.get_groups("2026-2027")] == [1]
    assert [teacher.id for teacher in await repository.get_teachers()] == [14]


async def test_sync_groups_full_semester(repository) -> None:
    client = FakeSemesterClient()
    sync = service(repository, client)

    report = await sync.sync_groups(ids=[1], force=True)

    assert report.synced == 1
    assert report.failed == 0
    assert report.lessons == 4
    assert await repository.covers("student", 1, date(2026, 9, 7))
    assert await repository.covers("student", 1, date(2026, 12, 21))
    assert not await repository.covers("student", 1, date(2027, 1, 10))

    lessons = await repository.get_lessons(
        "student", 1, date(2026, 9, 1), date(2026, 12, 31)
    )
    assert [item.subject for item in lessons] == [
        "лек Математический анализ",
        "пр История",
        "лек Физика",
        "зач Математический анализ",
    ]
    assert await repository.get_target_name("student", 1) == "26-ПМбо-1"


async def test_resync_is_idempotent(repository) -> None:
    client = FakeSemesterClient()
    sync = service(repository, client)

    await sync.sync_groups(ids=[1], force=True)
    await sync.sync_groups(ids=[1], force=True)

    stats = await repository.stats()
    assert stats.lessons == 4
    assert stats.targets == 1
    assert stats.semesters == 1


async def test_error_is_isolated_and_recorded(repository) -> None:
    client = FakeSemesterClient(failing=(("student", 2),))
    sync = service(repository, client)

    report = await sync.sync_groups(ids=[1, 2], force=True)

    assert report.synced == 1
    assert report.failed == 1
    assert report.lessons == 4
    assert any("student:2" in error for error in report.errors)

    rows = {row["target_id"]: row for row in await repository.list_targets()}
    assert rows[1]["status"] == "ok"
    assert rows[2]["status"] == "error"


async def test_min_interval_skips_recent_sync(repository) -> None:
    client = FakeSemesterClient()
    sync = service(repository, client)

    first = await sync.sync_groups(ids=[1], force=True)
    second = await sync.sync_groups(ids=[1], force=False)

    assert first.synced == 1
    assert second.skipped == 1
    assert len(client.calls) == 1


async def test_sync_teachers(repository) -> None:
    client = FakeSemesterClient()
    sync = service(repository, client)

    report = await sync.sync_teachers(ids=[14], force=True)

    assert report.synced == 1
    assert report.lessons == 4
    assert await repository.covers("teacher", 14, date(2026, 11, 9))
    assert await repository.get_target_name("teacher", 14) == "Адеева Т. Н."


async def test_sync_targets_mixed(repository) -> None:
    client = FakeSemesterClient()
    sync = service(repository, client)

    report = await sync.sync_targets(
        [(Role.STUDENT, 1), (Role.TEACHER, 14)], force=True
    )

    assert report.total == 2
    assert report.synced == 2
    assert report.lessons == 8
    stats = await repository.stats()
    assert stats.lessons == 8
    assert stats.targets == 2


async def test_empty_schedule_reported_as_empty(repository) -> None:
    client = FakeSemesterClient(schedule=Schedule())
    sync = service(repository, client)

    report = await sync.sync_groups(ids=[1], force=True)

    assert report.empty == 1
    assert report.lessons == 0
    stats = await repository.stats()
    assert stats.lessons == 0

    # Пустая цель помечена и не перезапрашивается в рамках интервала.
    assert await repository.was_synced("student", 1, 3_600)
    second = await sync.sync_groups(ids=[1], force=False)
    assert second.skipped == 1
    assert len(client.calls) == 1

    rows = await repository.list_targets()
    assert rows[0]["status"] == "empty"

