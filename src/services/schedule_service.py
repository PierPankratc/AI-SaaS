"""Сервис расписания: загрузка на день/неделю, кэш с резервом и
форматирование текста для сообщений ВКонтакте.

Расписание всегда запрашивается с параметром ``sdate`` (понедельник нужной
недели): без него API ЭИОС отдаёт весь семестр. Текстовые «экраны»
рассчитаны на навигацию кнопками («На сегодня», «На завтра», «На эту
неделю», «Сменить профиль») — ввод текста нужен только для фамилии.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from src.api.eios_client import EiosApiError
from src.api.schemas import Lesson, Schedule

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Europe/Moscow"
MAX_MESSAGE_LEN = 4000

WEEKDAYS = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


class Role(str, Enum):
    """Роль владельца расписания."""

    STUDENT = "student"
    TEACHER = "teacher"


def week_monday(day: date) -> date:
    """Понедельник недели, содержащей указанную дату."""
    return day - timedelta(days=day.weekday())


def lessons_on(lessons: Iterable[Lesson], day: date) -> list[Lesson]:
    """Пары на конкретную дату, упорядоченные по времени и подгруппе."""
    selected = [lesson for lesson in lessons if lesson.lesson_date.date() == day]
    return sorted(
        selected,
        key=lambda lesson: (lesson.starts_at, lesson.subgroup, lesson.subject),
    )


def format_date(day: date) -> str:
    """Человекочитаемая дата, например «11 ноября 2026»."""
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def _lesson_block(index: int, lesson: Lesson, role: Role) -> str:
    kind = lesson.lesson_kind
    title = lesson.subject_title
    subgroup = f" (п/г {lesson.subgroup})" if lesson.subgroup else ""
    replacement = " 🔁 замена" if lesson.is_replacement else ""
    room = f"ауд. {lesson.room}" if lesson.room else "ауд. не указана"

    lines = [f"{index}. {lesson.start_time}–{lesson.end_time}{replacement}"]
    prefix = f"{kind}: " if kind else ""
    lines.append(f"   {prefix}{title}{subgroup}")

    if role is Role.STUDENT:
        teacher = (
            lesson.teacher_full or lesson.teacher_short or "преподаватель не указан"
        )
        lines.append(f"   {room} · {teacher}")
    else:
        group = lesson.group_name or "группа не указана"
        lines.append(f"   {room} · гр. {group}")

    if lesson.link:
        lines.append(f"   🔗 {lesson.link}")
    return "\n".join(lines)


def format_day(
    lessons: list[Lesson],
    day: date,
    role: Role,
    title: str | None = None,
) -> str:
    """Текст расписания на один день (экран под кнопку)."""
    header = f"📅 {WEEKDAYS[day.weekday()]}, {format_date(day)}"
    if title:
        header = f"{title}\n{header}"
    if not lessons:
        return f"{header}\n\n😴 Занятий нет"

    blocks = [
        _lesson_block(index, lesson, role)
        for index, lesson in enumerate(lessons, 1)
    ]
    return f"{header}\n\n" + "\n\n".join(blocks)


def format_week(schedule: Schedule, role: Role, title: str | None = None) -> str:
    """Текст расписания на всю неделю, сгруппированный по дням."""
    header = title or "Расписание на неделю"
    if not schedule.lessons:
        return f"{header}\n\n😴 На этой неделе занятий нет"

    by_day: dict[date, list[Lesson]] = {}
    for lesson in schedule.lessons:
        by_day.setdefault(lesson.lesson_date.date(), []).append(lesson)

    sections: list[str] = [header]
    for day in sorted(by_day):
        day_lessons = lessons_on(by_day[day], day)
        blocks = [
            _lesson_block(index, lesson, role)
            for index, lesson in enumerate(day_lessons, 1)
        ]
        sections.append(
            f"📅 {WEEKDAYS[day.weekday()]}, {format_date(day)}\n\n"
            + "\n\n".join(blocks)
        )
    return "\n\n".join(sections)


def default_title(role: Role, schedule: Schedule) -> str:
    """Заголовок по блоку ``info`` (если профиль недоступен)."""
    info = schedule.info
    if info is not None:
        if role is Role.STUDENT and info.group and info.group.name:
            return f"👨‍🎓 Группа {info.group.name}"
        if role is Role.TEACHER and info.teacher and info.teacher.name:
            return f"👨‍🏫 {info.teacher.name}"
    return "Расписание"


def split_message(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Делит длинный текст на части по границам строк (лимит сообщения ВК)."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in text.split("\n"):
        if current and length + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current = []
            length = 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


class ScheduleService:
    """Загрузка расписания дня/недели с кэшем Redis и резервом."""

    def __init__(
        self,
        client: Any,
        cache: Any,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> None:
        self._client = client
        self._cache = cache
        self._tz = ZoneInfo(timezone)

    def today(self) -> date:
        """Текущая дата в часовом поясе университета."""
        return datetime.now(self._tz).date()

    def tomorrow(self) -> date:
        return self.today() + timedelta(days=1)

    def _cache_key(self, role: Role, target_id: int, monday: date) -> str:
        return f"{role.value}:{target_id}:{monday.isoformat()}"

    async def get_week(self, role: Role, target_id: int, day: date) -> Schedule:
        """Расписание на неделю, содержащую ``day`` (с кэшем и резервом)."""
        monday = week_monday(day)
        key = self._cache_key(role, target_id, monday)

        cached = await self._cache.get_schedule(key)
        if cached is not None:
            return Schedule.model_validate(cached)

        try:
            if role is Role.STUDENT:
                schedule = await self._client.get_group_schedule(
                    target_id, sdate=monday
                )
            else:
                schedule = await self._client.get_teacher_schedule(
                    target_id, sdate=monday
                )
        except EiosApiError:
            backup = await self._cache.get_schedule_backup(key)
            if backup is None:
                raise
            logger.warning(
                "ЭИОС недоступен, используется резервное расписание: %s", key
            )
            return Schedule.model_validate(backup)

        await self._cache.set_schedule(key, schedule.model_dump(mode="json"))
        return schedule

    async def get_day(
        self,
        role: Role,
        target_id: int,
        day: date,
    ) -> list[Lesson]:
        """Пары на конкретный день."""
        schedule = await self.get_week(role, target_id, day)
        return lessons_on(schedule.lessons, day)

    async def day_text(
        self,
        role: Role,
        target_id: int,
        day: date,
        title: str | None = None,
    ) -> str:
        """Готовый текст расписания на день (экран под кнопку)."""
        schedule = await self.get_week(role, target_id, day)
        resolved_title = title or default_title(role, schedule)
        return format_day(
            lessons_on(schedule.lessons, day), day, role, resolved_title
        )

    async def week_text(
        self,
        role: Role,
        target_id: int,
        day: date,
        title: str | None = None,
    ) -> str:
        """Готовый текст расписания на неделю (экран под кнопку)."""
        schedule = await self.get_week(role, target_id, day)
        resolved_title = title or default_title(role, schedule)
        return format_week(schedule, role, resolved_title)
