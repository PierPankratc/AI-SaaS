"""Тесты асинхронного клиента API ЭИОС КГУ (без реальной сети)."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import httpx
import pytest

from src.api.eios_client import EiosApiError, EiosClient

BASE_URL = "https://eios.kosgos.ru"


@asynccontextmanager
async def create_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> AsyncIterator[EiosClient]:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url=BASE_URL)
    client = EiosClient(client=http, retries=3, backoff=0)
    try:
        yield client
    finally:
        await http.aclose()


async def test_get_years() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/Rasp/ListYears"
        return httpx.Response(
            200,
            json={
                "data": {"years": ["2025-2026", "2026-2027"], "showPreviousYears": True},
                "state": 1,
                "msg": "Года",
                "time": 1,
            },
        )

    async with create_client(handler) as client:
        assert await client.get_years() == ["2025-2026", "2026-2027"]


async def test_get_groups_filters_placeholder() -> None:
    payload = {
        "data": [
            {
                "name": "26-ПМбо-1",
                "id": 8948,
                "kurs": 1,
                "facul": "ИВИТШ",
                "yearName": "2026-2027",
                "facultyID": 36,
            },
            {
                "name": "Нет",
                "id": 1,
                "kurs": None,
                "facul": None,
                "yearName": "2026-2027",
                "facultyID": None,
            },
        ],
        "state": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/raspGrouplist"
        assert request.url.params.get("year") == "2026-2027"
        return httpx.Response(200, json=payload)

    async with create_client(handler) as client:
        groups = await client.get_groups("2026-2027")

    assert [group.name for group in groups] == ["26-ПМбо-1"]


async def test_get_teachers_keeps_duplicates() -> None:
    payload = {
        "data": [
            {"name": "Бойцова Светлана Владимировна", "kaf": None, "id": 51, "idFromRasp": False},
            {"name": "Бойцова Светлана Владимировна", "kaf": None, "id": 52, "idFromRasp": False},
        ],
        "state": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with create_client(handler) as client:
        teachers = await client.get_teachers()

    assert {teacher.id for teacher in teachers} == {51, 52}


async def test_group_schedule_passes_sdate() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "data": {"isCyclicalSchedule": False, "rasp": [], "info": {}},
                "state": 1,
            },
        )

    async with create_client(handler) as client:
        schedule = await client.get_group_schedule(8948, sdate="2026-09-01")

    assert "idGroup=8948" in seen["url"]
    assert "sdate=2026-09-01" in seen["url"]
    assert schedule.lessons == []


async def test_retry_then_success() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": {"years": ["2026-2027"]}, "state": 1})

    async with create_client(handler) as client:
        assert await client.get_years() == ["2026-2027"]

    assert calls["n"] == 3


async def test_persistent_server_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with create_client(handler) as client:
        with pytest.raises(EiosApiError):
            await client.get_years()


async def test_api_state_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": None, "state": 0, "msg": "Ошибка"})

    async with create_client(handler) as client:
        with pytest.raises(EiosApiError):
            await client.get_teachers()


async def test_451_gives_proxy_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(451)

    async with create_client(handler) as client:
        with pytest.raises(EiosApiError) as excinfo:
            await client.get_years()

    assert "451" in str(excinfo.value)


async def test_client_accepts_proxy() -> None:
    client = EiosClient(proxy="http://127.0.0.1:8080")
    await client.aclose()
