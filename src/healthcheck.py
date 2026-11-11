"""Проверка доступности API ЭИОС с учётом прокси.

Запуск внутри контейнера:

    docker compose exec bot python -m src.healthcheck

Код возврата 0 — данные получены, 1 — ошибка (например, 451 без прокси).
"""

from __future__ import annotations

import asyncio
import logging

from src.api.eios_client import EiosApiError, EiosClient
from src.config import get_settings


async def _check() -> int:
    settings = get_settings()
    proxy = settings.eios_proxy_url or None

    print(f"EIOS_BASE_URL={settings.eios_base_url}")
    print(f"proxy={'enabled' if proxy else 'disabled'}")

    try:
        async with EiosClient(
            settings.eios_base_url,
            proxy=proxy,
            retries=1,
            timeout=15,
        ) as client:
            years = await client.get_years()
            groups = await client.get_groups(years[-1] if years else None)
            teachers = await client.get_teachers()
    except EiosApiError as exc:
        print(f"FAIL: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1

    print(
        f"OK: years={years}, groups={len(groups)}, teachers={len(teachers)}"
    )
    return 0


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(asyncio.run(_check()))


if __name__ == "__main__":
    main()
