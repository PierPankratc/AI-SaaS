"""Мониторинг и логирование ошибок.

* :func:`setup_sentry` — опциональная инициализация Sentry (если задан DSN);
* :func:`heartbeat_once` / :func:`heartbeat_loop` — «пинг» внешнего сервиса
  (healthchecks.io, Uptime Kuma push и т.п.). Это важно для long-poll бота:
  у него нет HTTP-эндпоинта, и внешний монитор иначе не увидит падение;
* :func:`write_alive` / :func:`alive_loop` — файл-«пульс»: бот обновляет его
  из event loop. Docker healthcheck и сторож (``scripts/watchdog.sh``)
  перезапускают контейнер, если процесс завис и «пульс» пропал.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

MIN_HEARTBEAT_INTERVAL = 30
MIN_ALIVE_INTERVAL = 5



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


def write_alive(path: str | Path) -> None:
    """Создаёт/обновляет файл-«пульс» (важна mtime файла).

    Используется Docker healthcheck: если бот завис и файл не обновляется,
    контейнер помечается ``unhealthy``, а ``scripts/watchdog.sh`` его
    перезапускает.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch()


async def alive_loop(path: str | Path, interval: int = 30) -> None:
    """Обновляет файл-«пульс» каждые ``interval`` секунд до отмены задачи."""
    if not path:
        return
    delay = max(interval, MIN_ALIVE_INTERVAL)
    while True:
        try:
            write_alive(path)
        except OSError as exc:  # диск/права — не роняем бота из-за пульса
            logger.warning("Не удалось обновить файл-пульс %s: %s", path, exc)
        await asyncio.sleep(delay)
