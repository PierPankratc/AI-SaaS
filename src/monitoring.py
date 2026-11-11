"""Мониторинг и логирование ошибок.

* :func:`setup_sentry` — опциональная инициализация Sentry (если задан DSN);
* :func:`heartbeat_once` / :func:`heartbeat_loop` — «пинг» внешнего сервиса
  (healthchecks.io, Uptime Kuma push и т.п.). Это важно для long-poll бота:
  у него нет HTTP-эндпоинта, и внешний монитор иначе не увидит падение.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

MIN_HEARTBEAT_INTERVAL = 30


def setup_sentry(dsn: str, environment: str = "production") -> None:
    """Инициализирует Sentry, если задан DSN и установлен ``sentry-sdk``."""
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("sentry-sdk не установлен — Sentry отключён")
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        traces_sample_rate=0.0,
        send_default_pii=False,
    )
    logger.info("Sentry инициализирован (environment=%s)", environment)


async def heartbeat_once(
    url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Отправляет один пинг. Возвращает ``True`` при успехе."""
    if not url:
        return False

    own_client = client is None
    http = client or httpx.AsyncClient(timeout=5.0)
    try:
        response = await http.get(url)
        return response.status_code < 400
    except httpx.HTTPError as exc:
        logger.warning("Heartbeat не отправлен: %s", exc)
        return False
    finally:
        if own_client:
            await http.aclose()


async def heartbeat_loop(url: str, interval: int) -> None:
    """Бесконечно пингует URL с заданным интервалом (отменяется задачей)."""
    if not url:
        return
    delay = max(interval, MIN_HEARTBEAT_INTERVAL)
    while True:
        await heartbeat_once(url)
        await asyncio.sleep(delay)
