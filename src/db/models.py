"""ORM-модели локальной базы данных (SQLite).

В одном файле ``data/bot.db`` хранятся профили пользователей, черновики
регистрации и расписание семестра. Внешние сервисы боту не нужны.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Базовый класс декларативных моделей профилей и черновиков."""


class User(Base):
    """Сохранённый профиль пользователя ВК (результат регистрации)."""

    __tablename__ = "users"

    vk_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)

    # --- студент ---
    faculty_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    faculty_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    edu_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    course: Mapped[int | None] = mapped_column(Integer, nullable=True)
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- преподаватель ---
    teacher_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    teacher_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Draft(Base):
    """Черновик незавершённой регистрации (FSM) с временем обновления."""

    __tablename__ = "drafts"

    vk_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)

