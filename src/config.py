"""Конфигурация приложения.

Все параметры читаются из переменных окружения или файла `.env`
(образец см. в `.env.example`). Доступ к настройкам — через синглтон
:func:`get_settings`.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки ВК-бота расписания ЭИОС КГУ."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- VK ---
    vk_bot_token: str  # Токен сообщества ВКонтакте (long-poll), обязателен

    # --- Базы данных и кэш ---
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/vk_schedule"
    )
    redis_url: str = "redis://localhost:6379/0"

    # --- ЭИОС КГУ ---
    eios_base_url: str = "https://eios.kosgos.ru"

    # --- Кэширование (TTL в секундах) ---
    catalog_cache_ttl: int = 86_400  # справочники (институты, группы, преподаватели) — 24 ч
    schedule_cache_ttl: int = 1_800  # расписание — 30 мин
    schedule_fallback_ttl: int = 604_800  # резервная копия расписания — 7 суток

    # --- Прочее ---
    timezone: str = "Europe/Moscow"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Возвращает закэшированный экземпляр настроек."""
    return Settings()
