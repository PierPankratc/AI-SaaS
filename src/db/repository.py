"""Репозиторий профилей пользователей и валидация профиля.

Все запросы выполняются через ORM SQLAlchemy с параметризацией — это
исключает SQL-инъекции. Поля профиля дополнительно ограничены по типам и
длине через Pydantic.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import User

ALLOWED_ROLES = frozenset({"student", "teacher"})


class UserProfile(BaseModel):
    """Провалидированный профиль пользователя."""

    model_config = ConfigDict(frozen=True)

    vk_id: int = Field(gt=0)
    role: str
    faculty_id: int | None = Field(default=None, ge=1)
    faculty_name: str | None = Field(default=None, max_length=128)
    edu_level: str | None = Field(default=None, max_length=32)
    course: int | None = Field(default=None, ge=1, le=6)
    group_id: int | None = Field(default=None, ge=1)
    group_name: str | None = Field(default=None, max_length=64)
    teacher_id: int | None = Field(default=None, ge=1)
    teacher_name: str | None = Field(default=None, max_length=256)

    @field_validator("role")
    @classmethod
    def _check_role(cls, value: str) -> str:
        if value not in ALLOWED_ROLES:
            raise ValueError("Недопустимая роль пользователя")
        return value


class UserRepository:
    """Чтение и сохранение профилей в базе."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get(self, vk_id: int) -> UserProfile | None:
        async with self._session_factory() as session:
            user = await session.get(User, vk_id)
            if user is None:
                return None
            return self._to_profile(user)

    async def list_all(self) -> list[UserProfile]:
        """Все сохранённые профили (для синхронизации по зарегистрированным)."""
        async with self._session_factory() as session:
            users = (
                await session.scalars(select(User).order_by(User.vk_id))
            ).all()
            return [self._to_profile(user) for user in users]

    async def save(self, profile: UserProfile) -> None:
        async with self._session_factory() as session:
            user = await session.get(User, profile.vk_id)
            if user is None:
                user = User(vk_id=profile.vk_id)
                session.add(user)

            user.role = profile.role
            user.faculty_id = profile.faculty_id
            user.faculty_name = profile.faculty_name
            user.edu_level = profile.edu_level
            user.course = profile.course
            user.group_id = profile.group_id
            user.group_name = profile.group_name
            user.teacher_id = profile.teacher_id
            user.teacher_name = profile.teacher_name

            await session.commit()

    async def reset(self, vk_id: int) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(User).where(User.vk_id == vk_id))
            await session.commit()

    @staticmethod
    def _to_profile(user: User) -> UserProfile:
        return UserProfile(
            vk_id=user.vk_id,
            role=user.role,
            faculty_id=user.faculty_id,
            faculty_name=user.faculty_name,
            edu_level=user.edu_level,
            course=user.course,
            group_id=user.group_id,
            group_name=user.group_name,
            teacher_id=user.teacher_id,
            teacher_name=user.teacher_name,
        )
