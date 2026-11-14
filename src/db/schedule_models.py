"""ORM-модели расписания локальной базы SQLite.

Все таблицы создаются в том же файле ``data/bot.db``, что и профили.
:class:`ScheduleBase` оставлен отдельным, чтобы модели расписания были
изолированы от профилей/черновиков, но обе схемы создаются одним
``SqliteDatabase.create_schema``.

Таблица ``lessons`` намеренно денормализована: каждая строка — одно занятие
для конкретной цели (группы или преподавателя). Это даёт простой и быстрый
запрос «расписание на день/неделю» через индекс
``(role, target_id, lesson_date)`` без разбора JSON.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class ScheduleBase(DeclarativeBase):
    """Базовый класс моделей локальной БД расписания."""


class Semester(ScheduleBase):
    """Семестр учебного года (например, ``2026-2027``, номер 1)."""

    __tablename__ = "semesters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    academic_year: Mapped[str] = mapped_column(String(16), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "academic_year", "number", name="uq_semesters_year_number"
        ),
    )


class Year(ScheduleBase):
    """Доступный учебный год (справочник ``ListYears``)."""

    __tablename__ = "academic_years"

    year: Mapped[str] = mapped_column(String(16), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GroupRow(ScheduleBase):
    """Учебная группа из справочника ЭИОС."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    faculty_id: Mapped[int] = mapped_column(Integer, nullable=False)
    faculty_name: Mapped[str] = mapped_column(String(128), nullable=False)
    course: Mapped[int] = mapped_column(Integer, nullable=False)
    education_level: Mapped[str] = mapped_column(String(32), nullable=False)
    program_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    academic_year: Mapped[str] = mapped_column(
        String(16), nullable=False, index=True
    )


class TeacherRow(ScheduleBase):
    """Преподаватель из справочника ЭИОС."""

    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    department: Mapped[str | None] = mapped_column(String(256), nullable=True)


class TargetRow(ScheduleBase):
    """Название цели расписания (для заголовков без обращения к ЭИОС)."""

    __tablename__ = "targets"

    role: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)


class LessonRow(ScheduleBase):
    """Одно занятие в расписании цели."""

    __tablename__ = "lessons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    semester_id: Mapped[int] = mapped_column(
        ForeignKey("semesters.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, nullable=False)

    lesson_date: Mapped[date] = mapped_column(Date, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    start_time: Mapped[str] = mapped_column(String(8), nullable=False)
    end_time: Mapped[str] = mapped_column(String(8), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    week_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    subgroup: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)

    teacher_short: Mapped[str | None] = mapped_column(String(256), nullable=True)
    teacher_full: Mapped[str | None] = mapped_column(String(256), nullable=True)
    teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    room: Mapped[str | None] = mapped_column(String(64), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lesson_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_replacement: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    academic_year: Mapped[str | None] = mapped_column(String(16), nullable=True)
    link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)

    __table_args__ = (
        Index("ix_lessons_lookup", "role", "target_id", "lesson_date"),
        Index("ix_lessons_semester", "semester_id"),
    )


class SyncState(ScheduleBase):
    """Состояние синхронизации цели за семестр.

    ``covered_from``/``covered_to`` — диапазон дат, за который в локальной БД
    гарантированно есть данные. По нему сервис расписания понимает, нужно ли
    догружать неделю из ЭИОС.
    """

    __tablename__ = "sync_state"

    role: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    semester_id: Mapped[int] = mapped_column(
        ForeignKey("semesters.id", ondelete="CASCADE"), primary_key=True
    )
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    lessons_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="semester"
    )
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    covered_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    covered_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class CatalogState(ScheduleBase):
    """Время последней синхронизации справочника (``years``, ``teachers``…)."""

    __tablename__ = "catalog_state"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

