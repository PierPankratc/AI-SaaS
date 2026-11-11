"""Черновики регистрации (FSM) с TTL.

Черновик хранит незавершённый выбор пользователя. В памяти — для одного
процесса и тестов; в Redis — при запуске нескольких инстансов бота.
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class OnboardingDraft(BaseModel):
    """Незавершённый выбор пользователя (сериализуется в Redis)."""

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


class RedisDrafts:
    """Черновики в Redis (общие для нескольких инстансов)."""

    def __init__(self, client: Any, *, ttl: int = 900, prefix: str = "eios") -> None:
        self._client = client
        self._ttl = ttl
        self._prefix = prefix

    def _key(self, vk_id: int) -> str:
        return f"{self._prefix}:draft:{vk_id}"

    async def get(self, vk_id: int) -> OnboardingDraft | None:
        raw = await self._client.get(self._key(vk_id))
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        try:
            return OnboardingDraft.model_validate(json.loads(raw))
        except (ValueError, TypeError):
            return None

    async def save(self, vk_id: int, draft: OnboardingDraft) -> None:
        payload = json.dumps(draft.model_dump(), ensure_ascii=False)
        await self._client.set(self._key(vk_id), payload, ex=self._ttl)

    async def clear(self, vk_id: int) -> None:
        await self._client.delete(self._key(vk_id))
