"""Тесты ограничителя частоты запросов."""

from src.services.rate_limiter import InMemoryCounterStore, RateLimiter


async def test_allows_within_limit() -> None:
    now = [0.0]
    limiter = RateLimiter(InMemoryCounterStore(lambda: now[0]), limit=3, window=10)

    assert await limiter.allow("u1")
    assert await limiter.allow("u1")
    assert await limiter.allow("u1")
    assert not await limiter.allow("u1")


async def test_window_slides() -> None:
    now = [0.0]
    limiter = RateLimiter(InMemoryCounterStore(lambda: now[0]), limit=2, window=10)

    assert await limiter.allow("u")
    assert await limiter.allow("u")
    assert not await limiter.allow("u")

    now[0] = 11.0
    assert await limiter.allow("u")
    assert await limiter.allow("u")
    assert not await limiter.allow("u")


async def test_keys_independent() -> None:
    now = [0.0]
    limiter = RateLimiter(InMemoryCounterStore(lambda: now[0]), limit=1, window=10)

    assert await limiter.allow("a")
    assert not await limiter.allow("a")
    assert await limiter.allow("b")
