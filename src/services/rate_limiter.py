"""Ограничение частоты запросов (защита от флуда и DoS).

Счётчики хранятся в памяти процесса: бот работает офлайн и не зависит от
внешних сервисов. Класс :class:`RateLimiter` не зависит от хранилища — при
необходимости можно передать другую реализацию ``CounterStore``.
"""

from __future__ import annotations

import time
from typing import Protocol


class CounterStore(Protocol):
    async def incr(self, key: str, window: int) -> int:
        """Увеличивает счётчик и возвращает число попаданий в окне."""
        ...


class InMemoryCounterStore:
    """Скользящее окно в памяти процесса."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, list[float]] = {}

    async def incr(self, key: str, window: int) -> int:
        now = self._clock()
        cutoff = now - window
        hits = [stamp for stamp in self._hits.get(key, []) if stamp > cutoff]
        hits.append(now)
        self._hits[key] = hits
        return len(hits)


class RateLimiter:
    """Проверяет, не превышен ли лимит запросов для ключа."""

    def __init__(
        self,
        store: CounterStore,
        *,
        limit: int = 20,
        window: int = 10,
    ) -> None:
        if limit < 1 or window < 1:
            raise ValueError("limit и window должны быть положительными")
        self._store = store
        self.limit = limit
        self.window = window

    async def allow(self, key: str) -> bool:
        count = await self._store.incr(f"ratelimit:{key}", self.window)
        return count <= self.limit
