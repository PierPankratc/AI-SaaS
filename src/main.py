"""Точка входа ВК-бота расписания КГУ."""

import asyncio
import logging

from src.api.eios_client import EiosClient
from src.bot.bot import build_bot
from src.bot.core import BotCore
from src.bot.drafts import RedisDrafts
from src.cache.redis_cache import RedisCache
from src.config import get_settings
from src.db.base import Database
from src.db.repository import UserRepository
from src.services.catalog_service import CatalogService
from src.services.rate_limiter import RateLimiter, RedisCounterStore
from src.services.schedule_service import ScheduleService

LOGGER_NAME = "vk_eios_bot"


def configure_logging(level: str) -> None:
    """Настраивает базовое логирование приложения."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def run() -> None:
    """Инициализирует зависимости и запускает бота (long poll)."""
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(LOGGER_NAME)

    if not settings.vk_bot_token or settings.vk_bot_token.startswith("your_"):
        raise RuntimeError("Укажите реальный VK_BOT_TOKEN в .env")

    import redis.asyncio as redis_asyncio

    database = Database(settings.database_url, echo=settings.db_echo)
    await database.create_schema()

    redis_client = redis_asyncio.from_url(
        settings.redis_url, decode_responses=True
    )
    cache = RedisCache(
        redis_client,
        catalog_ttl=settings.catalog_cache_ttl,
        schedule_ttl=settings.schedule_cache_ttl,
        fallback_ttl=settings.schedule_fallback_ttl,
    )
    drafts = RedisDrafts(redis_client, ttl=settings.onboarding_ttl)
    rate_limiter = RateLimiter(
        RedisCounterStore(redis_client),
        limit=settings.rate_limit_messages,
        window=settings.rate_limit_window,
    )
    repository = UserRepository(database.session_factory)

    try:
        async with EiosClient(settings.eios_base_url) as eios:
            catalog = CatalogService(eios, cache)
            schedules = ScheduleService(eios, cache, timezone=settings.timezone)

            try:
                await catalog.warmup()
            except Exception:  # noqa: BLE001 - бот должен запуститься и без прогрева
                logger.warning(
                    "Не удалось прогреть кэш справочников", exc_info=True
                )

            core = BotCore(catalog, schedules, repository, drafts, rate_limiter)
            bot = build_bot(settings.vk_bot_token, core)

            logger.info("Запуск бота ВК (long poll)…")
            await bot.run_polling()
    finally:
        await cache.close()
        await database.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()

