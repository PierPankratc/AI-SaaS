"""Точка входа ВК-бота расписания КГУ.

На текущем этапе (Этап 1) реализованы только загрузка конфигурации и
настройка логирования. Полноценный запуск бота (пул БД, подключение к
Redis, прогрев кэша, старт vkbottle) будет подключён на Этапе 5.
"""

import logging

from src.config import get_settings

LOGGER_NAME = "vk_eios_bot"


def configure_logging(level: str) -> None:
    """Настраивает базовое логирование приложения."""
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def main() -> None:
    """Инициализирует и (на следующих этапах) запускает бота."""
    settings = get_settings()
    configure_logging(settings.log_level)

    logger = logging.getLogger(LOGGER_NAME)
    logger.info(
        "Настройки загружены (EIOS=%s, TTL каталога=%d с)",
        settings.eios_base_url,
        settings.catalog_cache_ttl,
    )


if __name__ == "__main__":
    main()
