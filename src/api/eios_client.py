"""Асинхронный клиент открытого JSON API ЭИОС КГУ (eios.kosgos.ru).

Клиент намеренно не зависит от конфигурации приложения: базовый URL и
HTTP-клиент передаются в конструкторе. Это упрощает тестирование
(например, через ``httpx.MockTransport``) и переиспользование.

Использование::

    async with EiosClient() as client:
        years = await client.get_years()
        groups = await client.get_groups(years[-1])
        schedule = await client.get_group_schedule(groups[0].id)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import httpx

from src.api.schemas import Group, Schedule, Teacher, parse_groups, parse_teachers

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://eios.kosgos.ru"
DEFAULT_TIMEOUT = 10.0

DEFAULT_USER_AGENT = "VK-EIOS-Schedule-Bot/0.1 (+https://vk.com)"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REFERER = "https://eios.kosgos.ru/WebApp/"
ORIGIN = "https://eios.kosgos.ru"


def build_headers(
    *,
    user_agent: str | None = None,
    browser_headers: bool = False,
) -> dict[str, str]:
    """Собирает HTTP-заголовки запросов к ЭИОС.

    ``browser_headers`` включает браузероподобные заголовки (Chrome UA,
    Referer, Origin) — иногда помогает обойти WAF, который режет «не браузерные»
    запросы. IP источника при этом не меняется.
    """
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }
    if user_agent:
        headers["User-Agent"] = user_agent
    elif browser_headers:
        headers["User-Agent"] = BROWSER_USER_AGENT
    else:
        headers["User-Agent"] = DEFAULT_USER_AGENT

    if browser_headers:
        headers["Referer"] = REFERER
        headers["Origin"] = ORIGIN
    return headers

LIST_YEARS_PATH = "/api/Rasp/ListYears"
GROUPS_PATH = "/api/raspGrouplist"
TEACHERS_PATH = "/api/raspTeacherlist"
SCHEDULE_PATH = "/api/Rasp"


class EiosApiError(RuntimeError):
    """Ошибка обращения к API ЭИОС КГУ."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class EiosClient:
    """Клиент API расписания ЭИОС КГУ."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = 3,
        backoff: float = 0.5,
        proxy: str | None = None,
        user_agent: str | None = None,
        browser_headers: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.retries = max(1, retries)
        self.backoff = backoff
        self._external_client = client

        if client is not None:
            self._client = client
        else:
            client_kwargs: dict[str, Any] = {
                "base_url": self.base_url,
                "timeout": timeout,
                "headers": build_headers(
                    user_agent=user_agent, browser_headers=browser_headers
                ),
            }
            if proxy:
                client_kwargs["proxy"] = proxy
            self._client = httpx.AsyncClient(**client_kwargs)

    async def __aenter__(self) -> "EiosClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Закрывает внутренний HTTP-клиент (внешний не трогаем)."""
        if self._external_client is None:
            await self._client.aclose()

    # --- низкоуровневый запрос с повторами ---

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 1):
            try:
                response = await self._client.get(path, params=params)
            except httpx.TransportError as exc:
                last_error = exc
                logger.warning(
                    "Сетевая ошибка ЭИОС %s (попытка %d/%d): %s",
                    path,
                    attempt,
                    self.retries,
                    exc,
                )
            else:
                if response.status_code >= 500:
                    last_error = EiosApiError(
                        f"Сервер ЭИОС вернул {response.status_code}",
                        status_code=response.status_code,
                    )
                    logger.warning(
                        "Ошибка сервера ЭИОС %s (попытка %d/%d): %d",
                        path,
                        attempt,
                        self.retries,
                        response.status_code,
                    )
                elif response.status_code == 451:
                    raise EiosApiError(
                        "ЭИОС вернул 451: доступ с этого IP заблокирован "
                        "(настройте прокси с российским IP — EIOS_PROXY_URL)",
                        status_code=451,
                    )
                elif response.status_code >= 400:
                    raise EiosApiError(
                        f"ЭИОС вернул статус {response.status_code}",
                        status_code=response.status_code,
                    )
                else:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise EiosApiError(
                            f"ЭИОС вернул некорректный JSON по {path}"
                        ) from exc
                    return self._unwrap(payload, path)

            if attempt < self.retries:
                await asyncio.sleep(self.backoff * (2 ** (attempt - 1)))

        raise EiosApiError(
            f"Не удалось получить данные ЭИОС по {path}"
        ) from last_error

    @staticmethod
    def _unwrap(payload: Any, path: str) -> Any:
        if not isinstance(payload, dict):
            raise EiosApiError(f"ЭИОС вернул неожиданный ответ по {path}")
        if payload.get("state", 1) != 1:
            raise EiosApiError(
                f"API ЭИОС вернул ошибку: {payload.get('msg') or 'неизвестно'}"
            )
        return payload.get("data")

    # --- публичные методы ---

    async def get_years(self) -> list[str]:
        """Возвращает список доступных учебных годов (например, ``2026-2027``)."""
        data = await self._get_json(LIST_YEARS_PATH)
        if isinstance(data, dict):
            years = data.get("years") or []
            return [str(year) for year in years]
        return []

    async def get_groups(self, year: str | None = None) -> list[Group]:
        """Возвращает нормализованный список учебных групп."""
        params = {"year": year} if year else None
        data = await self._get_json(GROUPS_PATH, params)
        return parse_groups(data)

    async def get_teachers(self) -> list[Teacher]:
        """Возвращает нормализованный список преподавателей."""
        data = await self._get_json(TEACHERS_PATH)
        return parse_teachers(data)

    async def get_group_schedule(
        self,
        group_id: int,
        sdate: date | str | None = None,
    ) -> Schedule:
        """Расписание группы; ``sdate`` — любой день нужной недели."""
        return await self._get_schedule({"idGroup": group_id}, sdate)

    async def get_teacher_schedule(
        self,
        teacher_id: int,
        sdate: date | str | None = None,
    ) -> Schedule:
        """Расписание преподавателя; ``sdate`` — любой день нужной недели."""
        return await self._get_schedule({"idTeacher": teacher_id}, sdate)

    async def _get_schedule(
        self,
        params: dict[str, Any],
        sdate: date | str | None,
    ) -> Schedule:
        query = dict(params)
        if sdate is not None:
            query["sdate"] = sdate.isoformat() if isinstance(sdate, date) else sdate
        data = await self._get_json(SCHEDULE_PATH, query)
        return Schedule.model_validate(data or {})
