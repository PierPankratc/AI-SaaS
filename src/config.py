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
    vk_bot_token: str = ""  # Токен сообщества ВКонтакте (проверяется при запуске)

    # --- Локальная база данных (SQLite внутри проекта) ---
    sqlite_db_path: str = "data/bot.db"  # профили, черновики, расписание
    db_echo: bool = False  # логировать SQL (отладка)

    # --- Синхронизация семестра из ЭИОС (только CLI) ---
    schedule_sync_concurrency: int = 1  # ЭИОС режет частые запросы (429)
    schedule_sync_min_interval: int = 3_600  # не пересинхронизировать чаще, сек
    schedule_sync_request_delay: float = 3.0  # пауза между запросами, сек

    # --- ЭИОС КГУ ---
    eios_base_url: str = "https://eios.kosgos.ru"
    eios_proxy_url: str = ""  # прокси, если IP сервера блокируется (код 451)
    eios_user_agent: str = ""  # переопределить User-Agent (пусто — по умолчанию)
    eios_browser_headers: bool = False  # браузерные заголовки (Chrome UA/Referer/Origin)

    # --- Прочее ---
    timezone: str = "Europe/Moscow"
    log_level: str = "INFO"

    # --- Безопасность ---
    rate_limit_messages: int = 20  # максимум сообщений за окно
    rate_limit_window: int = 10  # окно в секундах
    max_text_length: int = 512  # максимум длины входящего текста
    onboarding_ttl: int = 900  # TTL черновика регистрации, 15 минут

    # --- Мониторинг и логирование ошибок ---
    environment: str = "production"  # окружение для Sentry
    sentry_dsn: str = ""  # если пусто — Sentry отключён
    heartbeat_url: str = ""  # URL пинга (healthchecks.io и т.п.)
    heartbeat_interval: int = 300  # период пинга в секундах


@lru_cache
def get_settings() -> Settings:
    """Возвращает закэшированный экземпляр настроек."""
    return Settings()
