"""Тесты сервиса расписания: фильтрация дня/недели, кэш, резерв и текст."""

from datetime import date

from src.api.eios_client import EiosApiError
from src.api.schemas import Schedule
from src.cache.redis_cache import RedisCache
from src.services.schedule_service import (
    Role,
    ScheduleService,
    format_day,
    format_week,
    lessons_on,
    split_message,
    week_monday,
)
from tests.fakes import FakeRedis


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
            },
        }
    )


class FakeScheduleClient:
    def __init__(self, schedule: Schedule, fail: bool = False) -> None:
        self.schedule = schedule
        self.fail = fail
        self.calls = 0

    async def get_group_schedule(self, group_id: int, sdate=None) -> Schedule:
        self.calls += 1
        if self.fail:
            raise EiosApiError("unavailable")
        return self.schedule

    async def get_teacher_schedule(self, teacher_id: int, sdate=None) -> Schedule:
        self.calls += 1
        if self.fail:
            raise EiosApiError("unavailable")
        return self.schedule


def test_week_monday() -> None:
    assert week_monday(date(2026, 11, 9)) == date(2026, 11, 9)
    assert week_monday(date(2026, 11, 10)) == date(2026, 11, 9)
    assert week_monday(date(2026, 11, 11)) == date(2026, 11, 9)


def test_lessons_on_filters_and_sorts() -> None:
    schedule = make_schedule()
    tuesday = lessons_on(schedule.lessons, date(2026, 11, 10))
    assert [lesson.subgroup for lesson in tuesday] == [1, 2, 0]
    assert [lesson.id for lesson in lessons_on(schedule.lessons, date(2026, 11, 9))] == [1]


def test_format_day_student() -> None:
    tuesday = lessons_on(make_schedule().lessons, date(2026, 11, 10))
    text = format_day(tuesday, date(2026, 11, 10), Role.STUDENT, "Группа 26-ПМбо-1")

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


async def test_week_cached_within_same_week() -> None:
    client = FakeScheduleClient(make_schedule())
    service = ScheduleService(client, RedisCache(FakeRedis()))

    await service.get_week(Role.STUDENT, 1, date(2026, 11, 9))
    await service.get_week(Role.STUDENT, 1, date(2026, 11, 11))

    assert client.calls == 1


async def test_fallback_returns_stale_on_api_error() -> None:
    fake = FakeRedis()
    cache = RedisCache(fake, schedule_ttl=30, fallback_ttl=1000)
    client = FakeScheduleClient(make_schedule())
    service = ScheduleService(client, cache)

    await service.get_week(Role.STUDENT, 1, date(2026, 11, 9))
    fake.advance(31)  # основной ключ истёк, резерв ещё жив
    client.fail = True

    result = await service.get_week(Role.STUDENT, 1, date(2026, 11, 9))
    assert len(result.lessons) == 4


async def test_day_and_week_text() -> None:
    service = ScheduleService(
        FakeScheduleClient(make_schedule()), RedisCache(FakeRedis())
    )

    day_text = await service.day_text(
        Role.STUDENT, 8948, date(2026, 11, 10), title="Группа 26-ПМбо-1"
    )
    assert "Вторник" in day_text

    week_text = await service.week_text(Role.STUDENT, 8948, date(2026, 11, 9))
    assert "Понедельник" in week_text and "Вторник" in week_text
