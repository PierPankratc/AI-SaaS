"""Тесты слоя кэширования :class:`RedisCache` (на фейковом Redis)."""

from src.cache.redis_cache import RedisCache
from tests.fakes import FakeRedis


async def test_catalog_json_roundtrip_and_ttl() -> None:
    fake = FakeRedis()
    cache = RedisCache(fake, catalog_ttl=100)

    await cache.set_catalog("years", ["2025-2026", "2026-2027"])
    assert await cache.get_catalog("years") == ["2025-2026", "2026-2027"]

    fake.advance(101)
    assert await cache.get_catalog("years") is None


async def test_schedule_primary_and_backup() -> None:
    fake = FakeRedis()
    cache = RedisCache(fake, schedule_ttl=30, fallback_ttl=1000)

    payload = {"lessons": [{"id": 1}], "week": "2026-11-09"}
    await cache.set_schedule("student:8641:2026-11-09", payload)

    assert await cache.get_schedule("student:8641:2026-11-09") == payload
    assert await cache.get_schedule_backup("student:8641:2026-11-09") == payload

    # Основной ключ истёк, резервная копия ещё жива (stale-if-error).
    fake.advance(31)
    assert await cache.get_schedule("student:8641:2026-11-09") is None
    assert await cache.get_schedule_backup("student:8641:2026-11-09") == payload


async def test_delete_removes_keys() -> None:
    fake = FakeRedis()
    cache = RedisCache(fake)

    await cache.set_catalog("x", {"a": 1})
    await cache.delete(cache._catalog_key("x"))

    assert await cache.get_catalog("x") is None


async def test_handles_bytes_payload() -> None:
    fake = FakeRedis()
    cache = RedisCache(fake)
    fake.store["raw:key"] = '{"ok": true}'

    assert await cache.get_json("raw:key") == {"ok": True}
