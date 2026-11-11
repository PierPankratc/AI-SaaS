"""Тесты мониторинга: heartbeat (Sentry не требует сети)."""

import httpx

from src.monitoring import heartbeat_once


async def test_heartbeat_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert await heartbeat_once("https://hc-ping.com/abc", client=client)
    finally:
        await client.aclose()


async def test_heartbeat_failure_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        assert not await heartbeat_once("https://hc-ping.com/abc", client=client)
    finally:
        await client.aclose()


async def test_heartbeat_empty_url() -> None:
    assert not await heartbeat_once("")


async def test_setup_sentry_without_dsn_is_noop() -> None:
    from src.monitoring import setup_sentry

    setup_sentry("")  # не должно бросать исключений
