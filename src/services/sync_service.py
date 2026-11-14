"""Синхронизация расписания ЭИОС в локальную SQLite.

Сервис используется CLI-утилитой ``python -m src.tools.sync_schedule``.
На каждую цель (группу или преподавателя) запрашивается сразу весь семестр:
API ЭИОС без параметра ``sdate`` отдаёт полное расписание. Результат атомарно
заменяет прежние строки цели, поэтому повторный запуск не создаёт дублей.
Сам бот этот сервис не вызывает и к ЭИОС не обращается.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from src.api.eios_client import EiosApiError
from src.api.schemas import Lesson, Schedule
from src.db.schedule_repository import (
    ScheduleRepository,
    SemesterKey,
    academic_year_for,
    semester_number_for,
)
from src.services.schedule_service import DEFAULT_TIMEZONE, Role

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TargetResult:
    """Итог синхронизации одной цели."""

    role: str
    target_id: int
    status: str  # ok | skipped | empty | error
    lessons: int = 0
    error: str | None = None


@dataclass
class SyncReport:
    """Сводный отчёт по нескольким целям."""

    results: list[TargetResult] = field(default_factory=list)

    def add(self, result: TargetResult) -> None:
        self.results.append(result)

    def merge(self, other: "SyncReport") -> None:
        self.results.extend(other.results)

    def _count(self, status: str) -> int:
        return sum(1 for result in self.results if result.status == status)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def synced(self) -> int:
        return self._count("ok")

    @property
    def skipped(self) -> int:
        return self._count("skipped")

    @property
    def empty(self) -> int:
        return self._count("empty")

    @property
    def failed(self) -> int:
        return self._count("error")

    @property
    def lessons(self) -> int:
        return sum(result.lessons for result in self.results)

    @property
    def errors(self) -> list[str]:
        return [
            f"{result.role}:{result.target_id} — {result.error}"
            for result in self.results
            if result.error
        ]

    def summary(self) -> str:
        return (
            f"целей {self.total}, успешно {self.synced}, "
            f"пропущено {self.skipped}, пусто {self.empty}, "
            f"ошибок {self.failed}, занятий {self.lessons}"
        )


class ScheduleSyncService:
    """Загрузка семестра расписания из ЭИОС в локальную SQLite."""

    def __init__(
        self,
        client: Any,
        repository: ScheduleRepository,
        *,
        concurrency: int = 5,
        min_interval: int = 3_600,
        request_delay: float = 0.2,
        timezone: str = DEFAULT_TIMEZONE,
    ) -> None:
        self._client = client
        self._repository = repository
        self._concurrency = max(1, concurrency)
        self._min_interval = max(0, min_interval)
        self._request_delay = max(0.0, request_delay)
        self._tz = ZoneInfo(timezone)

    def today(self) -> date:
        """Текущая дата в часовом поясе университета."""
        return datetime.now(self._tz).date()

    # --- справочники ---

    async def sync_catalog(self, year: str | None = None) -> str:
        """Скачивает годы, группы и преподавателей; возвращает выбранный год."""
        years = await self._client.get_years()
        if not years:
            raise EiosApiError("ЭИОС не вернул список учебных годов")

        selected = year or years[-1]
        groups = await self._client.get_groups(selected)
        teachers = await self._client.get_teachers()
        await self._repository.save_catalog(
            years=years,
            groups=groups,
            teachers=teachers,
            year=selected,
        )
        logger.info(
            "Справочники синхронизированы: годов %s, групп %s, преподавателей %s",
            len(years),
            len(groups),
            len(teachers),
        )
        return selected

    # --- расписание ---

    async def sync_groups(
        self,
        ids: Iterable[int] | None = None,
        *,
        year: str | None = None,
        force: bool = False,
    ) -> SyncReport:
        """Синхронизирует расписание групп (``None`` — все из справочника)."""
        if ids is None:
            groups = await self._repository.get_groups(year)
            ids = [group.id for group in groups]
        return await self._run(Role.STUDENT, list(ids), force=force)

    async def sync_teachers(
        self,
        ids: Iterable[int] | None = None,
        *,
        force: bool = False,
    ) -> SyncReport:
        """Синхронизирует расписание преподавателей."""
        if ids is None:
            teachers = await self._repository.get_teachers()
            ids = [teacher.id for teacher in teachers]
        return await self._run(Role.TEACHER, list(ids), force=force)

    async def sync_targets(
        self,
        targets: Iterable[tuple[Role, int]],
        *,
        force: bool = False,
    ) -> SyncReport:
        """Синхронизирует явный список целей ``(роль, id)``."""
        group_ids: list[int] = []
        teacher_ids: list[int] = []
        for role, target_id in targets:
            if role is Role.STUDENT:
                group_ids.append(target_id)
            else:
                teacher_ids.append(target_id)

        report = SyncReport()
        if group_ids:
            report.merge(await self._run(Role.STUDENT, group_ids, force=force))
        if teacher_ids:
            report.merge(await self._run(Role.TEACHER, teacher_ids, force=force))
        return report

    # --- внутренняя реализация ---

    async def _run(
        self,
        role: Role,
        target_ids: Sequence[int],
        *,
        force: bool,
    ) -> SyncReport:
        unique_ids = sorted(set(target_ids))
        if not unique_ids:
            return SyncReport()

        semaphore = asyncio.Semaphore(self._concurrency)
        report = SyncReport()

        async def worker(target_id: int) -> TargetResult:
            async with semaphore:
                try:
                    result = await self._sync_one(role, target_id, force=force)
                except EiosApiError as exc:
                    result = await self._fail(role, target_id, str(exc))
                except Exception as exc:  # noqa: BLE001 - одна цель не рушит прогон
                    logger.exception(
                        "Ошибка синхронизации %s %s", role.value, target_id
                    )
                    result = await self._fail(role, target_id, str(exc))
                logger.info(
                    "%s %s: %s (%s пар)",
                    role.value,
                    result.target_id,
                    result.status,
                    result.lessons,
                )
                if self._request_delay and result.status != "skipped":
                    await asyncio.sleep(self._request_delay)
                return result

        for result in await asyncio.gather(
            *(worker(target_id) for target_id in unique_ids)
        ):
            report.add(result)
        return report

    async def _sync_one(
        self, role: Role, target_id: int, *, force: bool
    ) -> TargetResult:
        if not force and await self._repository.was_synced(
            role.value, target_id, self._min_interval
        ):
            return TargetResult(role.value, target_id, "skipped")

        if role is Role.STUDENT:
            schedule = await self._client.get_group_schedule(target_id)
        else:
            schedule = await self._client.get_teacher_schedule(target_id)

        if not schedule.lessons:
            logger.warning(
                "ЭИОС вернул пустое расписание для %s %s", role.value, target_id
            )
            today = self.today()
            await self._repository.record_sync_empty(
                role=role.value,
                target_id=target_id,
                target_name=await self._target_name(role, target_id, schedule),
                semester=SemesterKey(
                    academic_year_for(today), semester_number_for(today)
                ),
            )
            return TargetResult(role.value, target_id, "empty")

        target_name = await self._target_name(role, target_id, schedule)
        total = 0
        for semester, lessons in self._split_by_semester(schedule).items():
            days = [lesson.lesson_date.date() for lesson in lessons]
            total += await self._repository.replace_target_schedule(
                role=role.value,
                target_id=target_id,
                target_name=target_name,
                semester=semester,
                lessons=lessons,
                covered_from=min(days),
                covered_to=max(days),
            )
        return TargetResult(role.value, target_id, "ok", lessons=total)

    async def _fail(
        self, role: Role, target_id: int, error: str
    ) -> TargetResult:
        today = self.today()
        try:
            name = (
                await self._repository.get_target_name(role.value, target_id)
                or f"ID {target_id}"
            )
            await self._repository.record_sync_error(
                role=role.value,
                target_id=target_id,
                target_name=name,
                semester=SemesterKey(
                    academic_year_for(today), semester_number_for(today)
                ),
                error=error,
            )
        except Exception:  # noqa: BLE001 - ошибку уже возвращаем в отчёте
            logger.exception("Не удалось записать ошибку синхронизации в БД")
        return TargetResult(role.value, target_id, "error", error=error)

    async def _target_name(
        self, role: Role, target_id: int, schedule: Schedule
    ) -> str:
        """Имя группы/преподавателя для заголовка расписания."""
        info = schedule.info
        if info is not None:
            if role is Role.STUDENT and info.group and info.group.name:
                return info.group.name
            if role is Role.TEACHER and info.teacher and info.teacher.name:
                return info.teacher.name

        stored = await self._repository.get_target_name(role.value, target_id)
        if stored:
            return stored

        if role is Role.STUDENT:
            groups = await self._repository.get_groups()
            match = next((group for group in groups if group.id == target_id), None)
            if match:
                return match.name
        else:
            teachers = await self._repository.get_teachers()
            match = next(
                (teacher for teacher in teachers if teacher.id == target_id), None
            )
            if match:
                return match.name
        return f"ID {target_id}"

    @staticmethod
    def _split_by_semester(
        schedule: Schedule,
    ) -> dict[SemesterKey, list[Lesson]]:
        """Группирует занятия ответа API по учебному году и номеру семестра."""
        info = schedule.info
        default_year = info.year if info else None
        default_number = info.semester if info else None

        buckets: dict[SemesterKey, list[Lesson]] = {}
        for lesson in schedule.lessons:
            day = lesson.lesson_date.date()
            key = SemesterKey(
                academic_year=(
                    lesson.academic_year
                    or default_year
                    or academic_year_for(day)
                ),
                number=(
                    lesson.semester
                    or default_number
                    or semester_number_for(day)
                ),
            )
            buckets.setdefault(key, []).append(lesson)
        return buckets


