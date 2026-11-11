"""Слой кэширования в Redis (``redis.asyncio``).

Клиент Redis передаётся извне (duck-typing), поэтому модуль не импортирует
``redis`` на уровне модуля — это упрощает тесты с фейковым клиентом.
Настоящий клиент удобно создать через :meth:`RedisCache.from_url`; он должен
работать с ``decode_responses=True`` (байты при чтении декодируются и так).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

CATALOG_PREFIX = "catalog"
SCHEDULE_PREFIX = "schedule"
SCHEMA_VERSION = "v1"


class RedisCache:
    """Обёртка над Redis для кэша справочников и расписания.

    * справочники кэшируются на ``catalog_ttl`` (по умолчанию сутки);
    * расписание кэшируется на ``schedule_ttl`` и дублируется в резервный
      ключ на ``fallback_ttl`` — это позволяет отдавать устаревшие данные,
      если сайт ЭИОС временно недоступен (stale-if-error).
    """

    def __init__(
        self,
        client: Any,
        *,
        catalog_ttl: int = 86_400,
        schedule_ttl: int = 1_800,
        fallback_ttl: int = 604_800,
        prefix: str = "eios",
    ) -> None:
        self._client = client
        self.catalog_ttl = catalog_ttl
        self.schedule_ttl = schedule_ttl
        self.fallback_ttl = fallback_ttl
        self.prefix = prefix

    @classmethod
    def from_url(cls, url: str, **kwargs: Any) -> "RedisCache":
        """Создаёт кэш с настоящим клиентом ``redis.asyncio``."""
        import redis.asyncio as redis_asyncio

        client = redis_asyncio.from_url(url, decode_responses=True)
        return cls(client, **kwargs)

    async def close(self) -> None:
        """Закрывает соединение с Redis (если клиент это поддерживает)."""
        closer = getattr(self._client, "aclose", None) or getattr(
            self._client, "close", None
        )
        if closer is not None:
            result = closer()
            if hasattr(result, "__await__"):
                await result

    # --- сериализация ---

    @staticmethod
    def _dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _loads(raw: Any) -> Any:
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            logger.warning("Не удалось разобрать JSON из кэша: %r", raw)
            return None

    # --- общие операции ---

    async def get_json(self, key: str) -> Any:
        return self._loads(await self._client.get(key))

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        payload = self._dumps(value)
        if ttl and ttl > 0:
            await self._client.set(key, payload, ex=ttl)
        else:
            await self._client.set(key, payload)

    async def delete(self, *keys: str) -> None:
        if keys:
            await self._client.delete(*keys)

    # --- справочники ---

    def _catalog_key(self, name: str) -> str:
        return f"{self.prefix}:{CATALOG_PREFIX}:{SCHEMA_VERSION}:{name}"

    async def get_catalog(self, name: str) -> Any:
        return await self.get_json(self._catalog_key(name))

    async def set_catalog(self, name: str, value: Any) -> None:
        await self.set_json(self._catalog_key(name), value, self.catalog_ttl)

    # --- расписание (с резервной копией) ---

    def _schedule_key(self, key: str) -> str:
        return f"{self.prefix}:{SCHEDULE_PREFIX}:{SCHEMA_VERSION}:{key}"

    def _schedule_backup_key(self, key: str) -> str:
        return f"{self._schedule_key(key)}:backup"

    async def get_schedule(self, key: str) -> Any:
        """Свежее расписание (основной ключ, короткий TTL)."""
        return await self.get_json(self._schedule_key(key))

    async def get_schedule_backup(self, key: str) -> Any:
        """Резервная копия расписания (долгий TTL, stale-if-error)."""
        return await self.get_json(self._schedule_backup_key(key))

    async def set_schedule(self, key: str, value: Any) -> None:
        """Сохраняет расписание в основной и резервный ключи."""
        await self.set_json(self._schedule_key(key), value, self.schedule_ttl)
        await self.set_json(self._schedule_backup_key(key), value, self.fallback_ttl)
