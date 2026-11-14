"""Тесты мониторинга: heartbeat, Sentry и файл-«пульс» (сети не требуют)."""

import asyncio
import os

import httpx
import pytest

from src.monitoring import (
    alive_loop,
    heartbeat_once,
    write_alive,
)


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


def test_write_alive_creates_parent_dirs(tmp_path) -> None:
    path = tmp_path / "nested" / "bot.alive"

    write_alive(path)

    assert path.is_file()


def test_write_alive_updates_mtime(tmp_path) -> None:
    path = tmp_path / "bot.alive"
    write_alive(path)
    os.utime(path, (0, 0))

    write_alive(path)

    assert path.stat().st_mtime > 0


async def test_alive_loop_writes_file_and_can_be_cancelled(tmp_path) -> None:
    path = tmp_path / "bot.alive"
    task = asyncio.create_task(alive_loop(path, interval=5))

    try:
        await asyncio.sleep(0.05)
        assert path.is_file()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_alive_loop_disabled_without_path() -> None:
    await asyncio.wait_for(alive_loop("", 5), timeout=1)
