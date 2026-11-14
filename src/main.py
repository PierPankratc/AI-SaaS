"""Точка входа ВК-бота расписания КГУ."""

import asyncio
import logging
from contextlib import suppress

from src.bot.bot import build_bot
from src.bot.core import BotCore
from src.bot.drafts import SqliteDrafts
from src.config import get_settings
from src.db.repository import UserRepository
from src.db.schedule_repository import ScheduleRepository
from src.db.sqlite_db import SqliteDatabase
from src.monitoring import heartbeat_loop, setup_sentry
from src.services.catalog_service import CatalogService
from src.services.rate_limiter import InMemoryCounterStore, RateLimiter
from src.services.schedule_service import ScheduleService

LOGGER_NAME = "vk_eios_bot"


def configure_logging(level: str) -> None:
    """Настраивает базовое логирование приложения."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def run() -> None:
    """Инициализирует зависимости и запускает бота (long poll, офлайн)."""
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger(LOGGER_NAME)
    setup_sentry(settings.sentry_dsn, settings.environment)

    if not settings.vk_bot_token or settings.vk_bot_token.startswith("your_"):
        raise RuntimeError("Укажите реальный VK_BOT_TOKEN в .env")

    database = SqliteDatabase(settings.sqlite_db_path, echo=settings.db_echo)
    await database.create_schema()

    schedule_repository = ScheduleRepository(database.session_factory)
    user_repository = UserRepository(database.session_factory)
    drafts = SqliteDrafts(database.session_factory, ttl=settings.onboarding_ttl)
    rate_limiter = RateLimiter(
        InMemoryCounterStore(),
        limit=settings.rate_limit_messages,
        window=settings.rate_limit_window,
    )

    catalog = CatalogService(schedule_repository)
    schedules = ScheduleService(schedule_repository, timezone=settings.timezone)

    stats = await schedule_repository.stats()
    logger.info(
        "Локальная база: %s пар, %s целей, %s групп, %s преподавателей (%s)",
        stats.lessons,
        stats.targets,
        stats.groups,
        stats.teachers,
        settings.sqlite_db_path,
    )
    if not stats.lessons:
        logger.warning(
            "В локальной базе нет расписания. Соберите её командой "
            "python -m src.tools.sync_schedule --all-groups на машине с "
            "доступом к ЭИОС и скопируйте файл %s на сервер.",
            settings.sqlite_db_path,
        )

    heartbeat_task: asyncio.Task | None = None
    try:
        if settings.heartbeat_url:
            heartbeat_task = asyncio.create_task(
                heartbeat_loop(
                    settings.heartbeat_url, settings.heartbeat_interval
                )
            )

        core = BotCore(
            catalog, schedules, user_repository, drafts, rate_limiter
        )
        bot = build_bot(settings.vk_bot_token, core)

        logger.info("Запуск бота ВК (long poll, офлайн-режим)…")
        await bot.run_polling()
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        await database.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()

