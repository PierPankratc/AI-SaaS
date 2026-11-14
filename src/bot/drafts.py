"""Черновики регистрации (FSM) с TTL.

Черновик хранит незавершённый выбор пользователя. В рабочем боте он лежит в
локальной SQLite (``drafts``), в тестах используется реализация в памяти.
"""

from __future__ import annotations

import time
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import Draft


class OnboardingDraft(BaseModel):
    """Незавершённый выбор пользователя (сериализуется в SQLite)."""

    model_config = ConfigDict(extra="forbid")

    role: str | None = None
    faculty_id: int | None = None
    faculty_name: str | None = None
    level: str | None = None
    course: int | None = None
    teacher_query: str | None = None
    page: int = 0


class DraftStore(Protocol):
    async def get(self, vk_id: int) -> OnboardingDraft | None: ...

    async def save(self, vk_id: int, draft: OnboardingDraft) -> None: ...

    async def clear(self, vk_id: int) -> None: ...


class InMemoryDrafts:
    """Черновики в памяти процесса с TTL."""

    def __init__(self, ttl: int = 900, clock=time.monotonic) -> None:
        self._ttl = ttl
        self._clock = clock
        self._data: dict[int, tuple[OnboardingDraft, float]] = {}

    async def get(self, vk_id: int) -> OnboardingDraft | None:
        item = self._data.get(vk_id)
        if item is None:
            return None
        draft, expires_at = item
        if expires_at <= self._clock():
            self._data.pop(vk_id, None)
            return None
        return draft

    async def save(self, vk_id: int, draft: OnboardingDraft) -> None:
        self._data[vk_id] = (draft, self._clock() + self._ttl)

    async def clear(self, vk_id: int) -> None:
        self._data.pop(vk_id, None)


class SqliteDrafts:
    """Черновики в локальной SQLite с TTL (переживают перезапуск бота)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        ttl: int = 900,
        clock=time.time,
    ) -> None:
        self._session_factory = session_factory
        self._ttl = ttl
        self._clock = clock

    async def get(self, vk_id: int) -> OnboardingDraft | None:
        async with self._session_factory() as session:
            row = await session.get(Draft, vk_id)
            if row is None:
                return None
            if row.updated_at + self._ttl <= self._clock():
                await session.delete(row)
                await session.commit()
                return None
            try:
                return OnboardingDraft.model_validate_json(row.payload)
            except ValueError:
                return None

    async def save(self, vk_id: int, draft: OnboardingDraft) -> None:
        payload = draft.model_dump_json()
        updated_at = self._clock()
        async with self._session_factory() as session:
            row = await session.get(Draft, vk_id)
            if row is None:
                session.add(
                    Draft(
                        vk_id=vk_id,
                        payload=payload,
                        updated_at=updated_at,
                    )
                )
            else:
                row.payload = payload
                row.updated_at = updated_at
            await session.commit()

    async def clear(self, vk_id: int) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(Draft).where(Draft.vk_id == vk_id))
            await session.commit()

