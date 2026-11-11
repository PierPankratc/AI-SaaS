"""Общие фейки для тестов: in-memory Redis и заглушка клиента ЭИОС."""

from __future__ import annotations

from collections import Counter

from src.api.schemas import Group, Teacher


class FakeRedis:
    """Минимальная реализация нужных методов Redis с учётом TTL."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expiry: dict[str, float | None] = {}
        self.now = 0.0

    def advance(self, seconds: float) -> None:
        """Сдвигает внутреннее время (для проверки истечения TTL)."""
        self.now += seconds

    async def get(self, key: str) -> str | None:
        expires_at = self.expiry.get(key)
        if expires_at is not None and expires_at <= self.now:
            self.store.pop(key, None)
            self.expiry.pop(key, None)
            return None
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        self.expiry[key] = (self.now + ex) if ex else None
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self.store:
                removed += 1
                self.store.pop(key, None)
                self.expiry.pop(key, None)
        return removed

    async def aclose(self) -> None:
        return None


class FakeEiosClient:
    """Заглушка :class:`EiosClient` со счётчиками вызовов."""

    def __init__(
        self,
        years: list[str],
        groups: list[Group],
        teachers: list[Teacher],
    ) -> None:
        self.years = years
        self.groups = groups
        self.teachers = teachers
        self.calls: Counter[str] = Counter()

    async def get_years(self) -> list[str]:
        self.calls["years"] += 1
        return list(self.years)

    async def get_groups(self, year: str | None = None) -> list[Group]:
        self.calls["groups"] += 1
        return list(self.groups)

    async def get_teachers(self) -> list[Teacher]:
        self.calls["teachers"] += 1
        return list(self.teachers)
