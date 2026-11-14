"""Репозиторий локального расписания: справочники и занятия семестра.

Все запросы выполняются через ORM SQLAlchemy с параметризацией. Запись
сериализуется внутрипроцессным ``asyncio.Lock``: синхронизация запускает
несколько задач параллельно, а SQLite в режиме WAL допускает только одного
писателя (читатели при этом не блокируются).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.schemas import EducationLevel, Group, Lesson, Teacher
from src.db.schedule_models import (
    CatalogState,
    GroupRow,
    LessonRow,
    Semester,
    SyncState,
    TargetRow,
    TeacherRow,
    Year,
)

WEEK_DAYS = 7


def utcnow() -> datetime:
    """Текущее время UTC без tzinfo (SQLite не хранит часовой пояс)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def academic_year_for(day: date) -> str:
    """Учебный год для даты: сентябрь–декабрь → текущий, январь–июль → прошлый."""
    start = day.year if day.month >= 8 else day.year - 1
    return f"{start}-{start + 1}"


def semester_number_for(day: date) -> int:
    """Номер семестра по дате (январь относится к осеннему семестру)."""
    return 1 if day.month in (8, 9, 10, 11, 12, 1) else 2


@dataclass(frozen=True)
class SemesterKey:
    """Ключ семестра: учебный год и номер."""

    academic_year: str
    number: int


@dataclass(frozen=True)
class SyncStats:
    """Сводка по локальной базе расписания."""

    lessons: int
    targets: int
    semesters: int
    groups: int
    teachers: int
    last_sync: datetime | None
    last_catalog_sync: datetime | None


class ScheduleRepository:
    """Чтение и запись расписания и справочников в локальной SQLite."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._write_lock = asyncio.Lock()

    # --- справочники ---

    async def save_years(self, years: Sequence[str]) -> None:
        async with self._write_lock, self._session_factory() as session:
            await self._replace_years(session, years)
            await self._touch_catalog(session, "years")
            await session.commit()

    async def save_groups(self, groups: Sequence[Group], *, year: str) -> None:
        async with self._write_lock, self._session_factory() as session:
            await self._replace_groups(session, groups, year)
            await self._touch_catalog(session, f"groups:{year}")
            await session.commit()

    async def save_teachers(self, teachers: Sequence[Teacher]) -> None:
        async with self._write_lock, self._session_factory() as session:
            await self._replace_teachers(session, teachers)
            await self._touch_catalog(session, "teachers")
            await session.commit()

    async def save_catalog(
        self,
        *,
        years: Sequence[str],
        groups: Sequence[Group],
        teachers: Sequence[Teacher],
        year: str,
    ) -> None:
        """Атомарно заменяет справочники, полученные из ЭИОС."""
        async with self._write_lock, self._session_factory() as session:
            await self._replace_years(session, years)
            await self._replace_groups(session, groups, year)
            await self._replace_teachers(session, teachers)
            await self._touch_catalog(session, "years")
            await self._touch_catalog(session, f"groups:{year}")
            await self._touch_catalog(session, "teachers")
            await session.commit()

    @staticmethod
    async def _replace_years(session: AsyncSession, years: Sequence[str]) -> None:
        await session.execute(delete(Year))
        session.add_all(
            [Year(year=year, position=index) for index, year in enumerate(years)]
        )

    @staticmethod
    async def _replace_groups(
        session: AsyncSession, groups: Sequence[Group], year: str
    ) -> None:
        await session.execute(
            delete(GroupRow).where(GroupRow.academic_year == year)
        )
        session.add_all(
            [
                GroupRow(
                    id=group.id,
                    name=group.name,
                    faculty_id=group.faculty_id,
                    faculty_name=group.faculty_name,
                    course=group.course,
                    education_level=group.education_level.value,
                    program_code=group.program_code,
                    academic_year=year,
                )
                for group in groups
            ]
        )

    @staticmethod
    async def _replace_teachers(
        session: AsyncSession, teachers: Sequence[Teacher]
    ) -> None:
        await session.execute(delete(TeacherRow))
        session.add_all(
            [
                TeacherRow(
                    id=teacher.id,
                    name=teacher.name,
                    department=teacher.department,
                )
                for teacher in teachers
            ]
        )

    @staticmethod
    async def _touch_catalog(session: AsyncSession, name: str) -> None:
        state = await session.get(CatalogState, name)
        if state is None:
            session.add(CatalogState(name=name, synced_at=utcnow()))
        else:
            state.synced_at = utcnow()

    async def get_years(self) -> list[str]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(Year).order_by(Year.position, Year.year)
                )
            ).all()
            if rows:
                return [row.year for row in rows]
            # Резерв: годы, которые встречаются у сохранённых групп.
            fallback = (
                await session.scalars(
                    select(GroupRow.academic_year)
                    .distinct()
                    .order_by(GroupRow.academic_year)
                )
            ).all()
            return [str(year) for year in fallback if year]

    async def get_groups(self, year: str | None = None) -> list[Group]:
        async with self._session_factory() as session:
            statement = select(GroupRow).order_by(GroupRow.name)
            if year:
                statement = statement.where(GroupRow.academic_year == year)
            rows = (await session.scalars(statement)).all()
            return [
                Group(
                    id=row.id,
                    name=row.name,
                    course=row.course,
                    faculty_id=row.faculty_id,
                    faculty_name=row.faculty_name,
                    year=row.academic_year,
                    education_level=EducationLevel(row.education_level),
                    program_code=row.program_code,
                )
                for row in rows
            ]

    async def get_teachers(self) -> list[Teacher]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(select(TeacherRow).order_by(TeacherRow.name))
            ).all()
            return [
                Teacher(id=row.id, name=row.name, department=row.department)
                for row in rows
            ]

    # --- запись расписания ---

    async def replace_target_schedule(
        self,
        *,
        role: str,
        target_id: int,
        target_name: str,
        semester: SemesterKey,
        lessons: Iterable[Lesson],
        covered_from: date | None = None,
        covered_to: date | None = None,
    ) -> int:
        """Заменяет всё расписание цели за семестр (идемпотентная пересинхронизация)."""
        items = list(lessons)
        async with self._write_lock, self._session_factory() as session:
            semester_row = await self._get_or_create_semester(session, semester)
            await session.execute(
                delete(LessonRow).where(
                    LessonRow.semester_id == semester_row.id,
                    LessonRow.role == role,
                    LessonRow.target_id == target_id,
                )
            )
            session.add_all(
                [
                    self._to_row(lesson, semester_row.id, role, target_id)
                    for lesson in items
                ]
            )
            await session.flush()
            await self._upsert_target(session, role, target_id, target_name)
            count = await self._target_lessons_count(
                session, semester_row.id, role, target_id
            )
            await self._upsert_sync_state(
                session,
                semester_row.id,
                role,
                target_id,
                count,
                covered_from,
                covered_to,
            )
            await self._refresh_bounds(session, semester_row)
            await session.commit()
            return len(items)

    async def record_sync_error(
        self,
        *,
        role: str,
        target_id: int,
        target_name: str,
        semester: SemesterKey,
        error: str,
    ) -> None:
        """Фиксирует ошибку синхронизации цели (без расширения покрытия)."""
        async with self._write_lock, self._session_factory() as session:
            semester_row = await self._get_or_create_semester(session, semester)
            await self._upsert_target(session, role, target_id, target_name)
            await self._upsert_sync_state(
                session,
                semester_row.id,
                role,
                target_id,
                None,
                None,
                None,
                status="error",
                error=error[:512],
                source="error",
            )
            await session.commit()

    async def record_sync_empty(
        self,
        *,
        role: str,
        target_id: int,
        target_name: str,
        semester: SemesterKey,
    ) -> None:
        """Помечает цель как «расписание пустое», чтобы не перезапрашивать её.

        Уже сохранённые занятия не удаляются: пустой ответ API не должен
        стирать данные, полученные ранее.
        """
        async with self._write_lock, self._session_factory() as session:
            semester_row = await self._get_or_create_semester(session, semester)
            await self._upsert_target(session, role, target_id, target_name)
            count = await self._target_lessons_count(
                session, semester_row.id, role, target_id
            )
            await self._upsert_sync_state(
                session,
                semester_row.id,
                role,
                target_id,
                count,
                None,
                None,
                status="empty",
                source="semester",
            )
            await session.commit()

    # --- служебные методы записи ---

    @staticmethod
    async def _get_or_create_semester(
        session: AsyncSession, key: SemesterKey
    ) -> Semester:
        statement = select(Semester).where(
            Semester.academic_year == key.academic_year,
            Semester.number == key.number,
        )
        semester = (await session.scalars(statement)).one_or_none()
        now = utcnow()
        if semester is None:
            semester = Semester(
                academic_year=key.academic_year,
                number=key.number,
                synced_at=now,
            )
            session.add(semester)
            await session.flush()
        else:
            semester.synced_at = now
        return semester

    @staticmethod
    async def _upsert_target(
        session: AsyncSession, role: str, target_id: int, name: str
    ) -> None:
        target = await session.get(TargetRow, (role, target_id))
        if target is None:
            session.add(TargetRow(role=role, target_id=target_id, name=name))
        elif name:
            target.name = name

    @staticmethod
    async def _target_lessons_count(
        session: AsyncSession, semester_id: int, role: str, target_id: int
    ) -> int:
        count = await session.scalar(
            select(func.count())
            .select_from(LessonRow)
            .where(
                LessonRow.semester_id == semester_id,
                LessonRow.role == role,
                LessonRow.target_id == target_id,
            )
        )
        return int(count or 0)

    @staticmethod
    async def _upsert_sync_state(
        session: AsyncSession,
        semester_id: int,
        role: str,
        target_id: int,
        lessons_count: int | None,
        covered_from: date | None,
        covered_to: date | None,
        *,
        status: str = "ok",
        error: str | None = None,
        source: str = "semester",
    ) -> None:
        state = await session.get(SyncState, (role, target_id, semester_id))
        if state is None:
            session.add(
                SyncState(
                    role=role,
                    target_id=target_id,
                    semester_id=semester_id,
                    synced_at=utcnow(),
                    lessons_count=lessons_count or 0,
                    status=status,
                    source=source,
                    error=error,
                    covered_from=covered_from if status == "ok" else None,
                    covered_to=covered_to if status == "ok" else None,
                )
            )
            return

        state.synced_at = utcnow()
        state.status = status
        state.error = error
        if lessons_count is not None:
            state.lessons_count = lessons_count
        if status == "ok":
            state.covered_from = _earliest(state.covered_from, covered_from)
            state.covered_to = _latest(state.covered_to, covered_to)
        if status in ("ok", "empty"):
            # Успешная синхронизация (в т.ч. пустая) больше не считается ошибкой.
            if source == "semester" or state.source != "semester":
                state.source = source

    @staticmethod
    async def _refresh_bounds(session: AsyncSession, semester: Semester) -> None:
        bounds = (
            await session.execute(
                select(
                    func.min(LessonRow.lesson_date),
                    func.max(LessonRow.lesson_date),
                ).where(LessonRow.semester_id == semester.id)
            )
        ).one()
        semester.starts_on = bounds[0]
        semester.ends_on = bounds[1]

    @staticmethod
    def _to_row(
        lesson: Lesson, semester_id: int, role: str, target_id: int
    ) -> LessonRow:
        return LessonRow(
            semester_id=semester_id,
            role=role,
            target_id=target_id,
            lesson_date=lesson.lesson_date.date(),
            starts_at=lesson.starts_at,
            ends_at=lesson.ends_at,
            start_time=lesson.start_time,
            end_time=lesson.end_time,
            weekday=lesson.weekday,
            week_type=lesson.week_type,
            subgroup=lesson.subgroup,
            subject=lesson.subject,
            teacher_short=lesson.teacher_short,
            teacher_full=lesson.teacher_full,
            teacher_id=lesson.teacher_id,
            room=lesson.room,
            group_name=lesson.group_name,
            group_id=lesson.group_id,
            lesson_number=lesson.lesson_number,
            is_replacement=1 if lesson.is_replacement else 0,
            academic_year=lesson.academic_year,
            link=lesson.link,
            color=lesson.color,
        )

    async def replace_target_week(
        self,
        *,
        role: str,
        target_id: int,
        target_name: str,
        semester: SemesterKey,
        monday: date,
        lessons: Iterable[Lesson],
    ) -> int:
        """Сохраняет одну неделю (путь догрузки при промахе в SQLite)."""
        sunday = monday + timedelta(days=WEEK_DAYS - 1)
        items = [
            lesson
            for lesson in lessons
            if monday <= lesson.lesson_date.date() <= sunday
        ]
        async with self._write_lock, self._session_factory() as session:
            semester_row = await self._get_or_create_semester(session, semester)
            await session.execute(
                delete(LessonRow).where(
                    LessonRow.semester_id == semester_row.id,
                    LessonRow.role == role,
                    LessonRow.target_id == target_id,
                    LessonRow.lesson_date >= monday,
                    LessonRow.lesson_date <= sunday,
                )
            )
            session.add_all(
                [
                    self._to_row(lesson, semester_row.id, role, target_id)
                    for lesson in items
                ]
            )
            await session.flush()
            await self._upsert_target(session, role, target_id, target_name)
            count = await self._target_lessons_count(
                session, semester_row.id, role, target_id
            )
            await self._upsert_sync_state(
                session,
                semester_row.id,
                role,
                target_id,
                count,
                monday,
                sunday,
                source="week",
            )
            await self._refresh_bounds(session, semester_row)
            await session.commit()
            return len(items)

    # --- чтение расписания ---

    async def get_lessons(
        self,
        role: str,
        target_id: int,
        start: date,
        end: date,
    ) -> list[Lesson]:
        """Занятия цели за диапазон дат, упорядоченные по времени."""
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(LessonRow)
                    .where(
                        LessonRow.role == role,
                        LessonRow.target_id == target_id,
                        LessonRow.lesson_date >= start,
                        LessonRow.lesson_date <= end,
                    )
                    .order_by(
                        LessonRow.lesson_date,
                        LessonRow.start_time,
                        LessonRow.subgroup,
                        LessonRow.subject,
                    )
                )
            ).all()
            return [_row_to_lesson(row) for row in rows]

    async def get_target_name(self, role: str, target_id: int) -> str | None:
        async with self._session_factory() as session:
            target = await session.get(TargetRow, (role, target_id))
            return target.name if target else None

    async def teacher_ids_with_lessons(self) -> list[int]:
        """ID преподавателей, у которых есть пары в загруженных занятиях групп."""
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(LessonRow.teacher_id)
                    .where(
                        LessonRow.role == "student",
                        LessonRow.teacher_id.is_not(None),
                    )
                    .distinct()
                    .order_by(LessonRow.teacher_id)
                )
            ).all()
            return [int(value) for value in rows if value is not None]

    async def rebuild_teacher_schedules(self) -> int:
        """Строит расписание преподавателей из занятий групп.

        ЭИОС не вызывается: каждое занятие группы уже содержит преподавателя,
        поэтому расписание преподавателя — это те же занятия, сгруппированные
        по нему. Совпадающие занятия нескольких групп (потоковые лекции)
        объединяются, а названия групп склеиваются через запятую.
        """
        async with self._write_lock, self._session_factory() as session:
            student_rows = (
                await session.scalars(
                    select(LessonRow).where(
                        LessonRow.role == "student",
                        LessonRow.teacher_id.is_not(None),
                    )
                )
            ).all()
            teacher_names = {
                teacher.id: teacher.name
                for teacher in (
                    await session.scalars(select(TeacherRow))
                ).all()
            }

            await session.execute(
                delete(LessonRow).where(LessonRow.role == "teacher")
            )
            await session.execute(
                delete(SyncState).where(SyncState.role == "teacher")
            )

            buckets: dict[tuple, dict] = {}
            for row in student_rows:
                key = (
                    row.semester_id,
                    row.teacher_id,
                    row.lesson_date,
                    row.start_time,
                    row.end_time,
                    row.subject,
                    row.subgroup,
                    row.room,
                    row.is_replacement,
                    row.link,
                    row.color,
                    row.teacher_short,
                    row.teacher_full,
                    row.lesson_number,
                    row.academic_year,
                    row.weekday,
                    row.week_type,
                )
                bucket = buckets.get(key)
                if bucket is None:
                    buckets[key] = {
                        "row": row,
                        "groups": {row.group_name} if row.group_name else set(),
                    }
                elif row.group_name:
                    bucket["groups"].add(row.group_name)

            per_target: dict[tuple[int, int], list[LessonRow]] = defaultdict(list)
            for bucket in buckets.values():
                source: LessonRow = bucket["row"]
                teacher_id = int(source.teacher_id)
                group_name = ", ".join(sorted(bucket["groups"])) or None
                target_row = LessonRow(
                    semester_id=source.semester_id,
                    role="teacher",
                    target_id=teacher_id,
                    lesson_date=source.lesson_date,
                    starts_at=source.starts_at,
                    ends_at=source.ends_at,
                    start_time=source.start_time,
                    end_time=source.end_time,
                    weekday=source.weekday,
                    week_type=source.week_type,
                    subgroup=source.subgroup,
                    subject=source.subject,
                    teacher_short=source.teacher_short,
                    teacher_full=source.teacher_full,
                    teacher_id=teacher_id,
                    room=source.room,
                    group_name=(group_name[:256] if group_name else None),
                    group_id=None,
                    lesson_number=source.lesson_number,
                    is_replacement=source.is_replacement,
                    academic_year=source.academic_year,
                    link=source.link,
                    color=source.color,
                )
                session.add(target_row)
                per_target[(source.semester_id, teacher_id)].append(target_row)

            await session.flush()
            for (semester_id, teacher_id), rows in per_target.items():
                name = teacher_names.get(teacher_id, f"ID {teacher_id}")
                days = [row.lesson_date for row in rows]
                await self._upsert_target(
                    session, "teacher", teacher_id, name
                )
                await self._upsert_sync_state(
                    session,
                    semester_id,
                    "teacher",
                    teacher_id,
                    len(rows),
                    min(days),
                    max(days),
                )

            semester_ids = {semester_id for semester_id, _ in per_target}
            semesters = (
                await session.scalars(
                    select(Semester).where(Semester.id.in_(semester_ids))
                )
            ).all()
            for semester in semesters:
                await self._refresh_bounds(session, semester)

            await session.commit()
            return sum(len(rows) for rows in per_target.values())

    async def covers(self, role: str, target_id: int, day: date) -> bool:
        """Есть ли в SQLite гарантированно полные данные на указанный день."""
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(SyncState)
                .where(
                    SyncState.role == role,
                    SyncState.target_id == target_id,
                    SyncState.status == "ok",
                    SyncState.covered_from.is_not(None),
                    SyncState.covered_to.is_not(None),
                    SyncState.covered_from <= day,
                    SyncState.covered_to >= day,
                )
            )
            return bool(count)

    async def was_synced(
        self, role: str, target_id: int, min_interval: int
    ) -> bool:
        """Синхронизировалась ли цель не раньше, чем ``min_interval`` секунд назад."""
        threshold = utcnow() - timedelta(seconds=max(0, min_interval))
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(SyncState)
                .where(
                    SyncState.role == role,
                    SyncState.target_id == target_id,
                    SyncState.status.in_(("ok", "empty")),
                    SyncState.source == "semester",
                    SyncState.synced_at >= threshold,
                )
            )
            return bool(count)

    async def list_targets(self) -> list[dict[str, Any]]:
        """Список синхронизированных целей (для CLI ``--list-targets``)."""
        async with self._session_factory() as session:
            states = (
                await session.scalars(
                    select(SyncState).order_by(
                        SyncState.role, SyncState.target_id
                    )
                )
            ).all()
            names = {
                (target.role, target.target_id): target.name
                for target in (await session.scalars(select(TargetRow))).all()
            }
            semesters = {
                semester.id: semester
                for semester in (await session.scalars(select(Semester))).all()
            }
            result: list[dict[str, Any]] = []
            for state in states:
                semester = semesters.get(state.semester_id)
                result.append(
                    {
                        "role": state.role,
                        "target_id": state.target_id,
                        "name": names.get(
                            (state.role, state.target_id),
                            f"ID {state.target_id}",
                        ),
                        "academic_year": (
                            semester.academic_year if semester else None
                        ),
                        "semester": semester.number if semester else None,
                        "status": state.status,
                        "source": state.source,
                        "lessons_count": state.lessons_count,
                        "synced_at": state.synced_at,
                        "covered_from": state.covered_from,
                        "covered_to": state.covered_to,
                    }
                )
            return result

    async def stats(self) -> SyncStats:
        """Сводная статистика локальной базы расписания."""
        async with self._session_factory() as session:
            lessons = await session.scalar(
                select(func.count()).select_from(LessonRow)
            )
            targets = await session.scalar(
                select(func.count()).select_from(TargetRow)
            )
            semesters = await session.scalar(
                select(func.count()).select_from(Semester)
            )
            groups = await session.scalar(
                select(func.count()).select_from(GroupRow)
            )
            teachers = await session.scalar(
                select(func.count()).select_from(TeacherRow)
            )
            last_sync = await session.scalar(select(func.max(SyncState.synced_at)))
            last_catalog = await session.scalar(
                select(func.max(CatalogState.synced_at))
            )
            return SyncStats(
                lessons=int(lessons or 0),
                targets=int(targets or 0),
                semesters=int(semesters or 0),
                groups=int(groups or 0),
                teachers=int(teachers or 0),
                last_sync=last_sync,
                last_catalog_sync=last_catalog,
            )


WEEKDAY_NAMES = (
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
)


def _row_to_lesson(row: LessonRow) -> Lesson:
    """Преобразует строку БД в доменную модель :class:`Lesson`."""
    return Lesson(
        id=row.id,
        lesson_date=datetime.combine(row.lesson_date, time.min),
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        start_time=row.start_time,
        end_time=row.end_time,
        weekday=row.weekday,
        weekday_name=WEEKDAY_NAMES[(row.weekday - 1) % 7],
        subgroup=row.subgroup,
        subject=row.subject,
        teacher_short=row.teacher_short,
        teacher_full=row.teacher_full,
        teacher_id=row.teacher_id,
        room=row.room,
        group_name=row.group_name,
        group_id=row.group_id,
        lesson_number=row.lesson_number,
        is_replacement=bool(row.is_replacement),
        academic_year=row.academic_year,
        link=row.link,
        color=row.color,
    )


def _earliest(current: date | None, candidate: date | None) -> date | None:
    if current is None:
        return candidate
    if candidate is None:
        return current
    return min(current, candidate)


def _latest(current: date | None, candidate: date | None) -> date | None:
    if current is None:
        return candidate
    if candidate is None:
        return current
    return max(current, candidate)





