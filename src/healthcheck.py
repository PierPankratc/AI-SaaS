"""Проверка доступности API ЭИОС с учётом прокси.

Нужна только для синхронизации (CLI) — сам бот к ЭИОС не обращается.

Запуск (на машине/в контейнере, где будет выполняться синхронизация):

    docker compose exec bot python -m src.healthcheck

Код возврата 0 — данные получены, 1 — ошибка (например, 451 без прокси).
"""

from __future__ import annotations

import asyncio
import logging

from src.api.eios_client import EiosApiError, EiosClient
from src.config import get_settings


async def _try(
    label: str,
    base_url: str,
    *,
    proxy: str | None,
    browser_headers: bool,
) -> bool:
    try:
        async with EiosClient(
            base_url,
            proxy=proxy,
            browser_headers=browser_headers,
            retries=1,
            timeout=15,
        ) as client:
            years = await client.get_years()
    except EiosApiError as exc:
        print(f"[{label}] FAIL: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] FAIL: {type(exc).__name__}: {exc}")
        return False

    print(f"[{label}] OK: years={years}")
    return True


async def _check() -> int:
    settings = get_settings()
    proxy = settings.eios_proxy_url or None
    base = settings.eios_base_url

    print(f"EIOS_BASE_URL={base}")
    print(f"proxy={'enabled' if proxy else 'disabled'}")

    strategies: list[tuple[str, dict]] = [
        ("bot-UA / direct", {"proxy": None, "browser_headers": False}),
        ("browser / direct", {"proxy": None, "browser_headers": True}),
    ]
    if proxy:
        strategies.append(
            ("bot-UA / proxy", {"proxy": proxy, "browser_headers": False})
        )
        strategies.append(
            ("browser / proxy", {"proxy": proxy, "browser_headers": True})
        )

    ok = False
    for label, kwargs in strategies:
        ok = await _try(label, base, **kwargs) or ok

    if ok:
        print("RESULT: доступ есть (см. успешную стратегию выше)")
        return 0
    print("RESULT: ЭИОС недоступен всеми способами")
    return 1


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(asyncio.run(_check()))


if __name__ == "__main__":
    main()
