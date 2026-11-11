"""Тесты сервиса каталога и поиска преподавателей."""

from src.api.schemas import EducationLevel, Group, parse_groups, parse_teachers
from src.cache.redis_cache import RedisCache
from src.services.catalog_service import (
    CatalogService,
    available_courses,
    available_levels,
    build_faculties,
    normalize_person_name,
    search_teachers,
    select_groups,
)
from tests.fakes import FakeEiosClient, FakeRedis

RAW_GROUPS = [
    {"name": "26-ПМбо-1", "id": 1, "kurs": 1, "facul": "ИВИТШ", "yearName": "2026-2027", "facultyID": 36},
    {"name": "26-ПМбо-2", "id": 2, "kurs": 1, "facul": "ИВИТШ", "yearName": "2026-2027", "facultyID": 36},
    {"name": "25-ПМбо-1", "id": 3, "kurs": 2, "facul": "ИВИТШ", "yearName": "2026-2027", "facultyID": 36},
    {"name": "26-ПМмо-1", "id": 4, "kurs": 1, "facul": "ИВИТШ", "yearName": "2026-2027", "facultyID": 36},
    {"name": "26-ЭБсо-1", "id": 5, "kurs": 1, "facul": "ИУЭФ", "yearName": "2026-2027", "facultyID": 32},
    {"name": "26-МОао-1", "id": 6, "kurs": 1, "facul": "АСП", "yearName": "2026-2027", "facultyID": 35},
    {"name": "Нет", "id": 7, "kurs": None, "facul": None, "yearName": "2026-2027", "facultyID": None},
]

RAW_TEACHERS = [
    {"id": 1, "name": "Адеева Татьяна Николаевна", "kaf": None, "idFromRasp": False},
    {"id": 2, "name": "Адеев Иван Петрович", "kaf": None, "idFromRasp": False},
    {"id": 3, "name": "Фёдоров Пётр Ильич", "kaf": None, "idFromRasp": False},
    {"id": 4, "name": "Бойцова Светлана Владимировна", "kaf": None, "idFromRasp": False},
    {"id": 5, "name": "Бойцова Светлана Владимировна", "kaf": None, "idFromRasp": False},
]


def _groups() -> list[Group]:
    return parse_groups(RAW_GROUPS)


def _service() -> CatalogService:
    client = FakeEiosClient(
        ["2026-2027"], _groups(), parse_teachers(RAW_TEACHERS)
    )
    return CatalogService(client, RedisCache(FakeRedis()))


def test_normalize_person_name() -> None:
    assert normalize_person_name("  ФёДОРОВ   Пётр ") == "федоров петр"


def test_build_faculties() -> None:
    faculties = build_faculties(_groups())
    assert [(f.id, f.name) for f in faculties] == [
        (35, "АСП"),
        (36, "ИВИТШ"),
        (32, "ИУЭФ"),
    ]


def test_available_levels_and_courses() -> None:
    groups = _groups()
    assert available_levels(groups, 36) == [
        EducationLevel.BACHELOR,
        EducationLevel.MAGISTRACY,
    ]
    assert available_levels(groups, 35) == [EducationLevel.POSTGRADUATE]
    assert available_courses(groups, 36, EducationLevel.BACHELOR) == [1, 2]
    assert available_courses(groups, 36, EducationLevel.MAGISTRACY) == [1]


def test_select_groups() -> None:
    names = [g.name for g in select_groups(_groups(), 36, EducationLevel.BACHELOR, 1)]
    assert names == ["26-ПМбо-1", "26-ПМбо-2"]


def test_search_by_surname_exact_first() -> None:
    teachers = parse_teachers(RAW_TEACHERS)
    assert [t.id for t in search_teachers(teachers, "адеев")] == [2, 1]


def test_search_ignores_case_and_yo() -> None:
    teachers = parse_teachers(RAW_TEACHERS)
    assert [t.id for t in search_teachers(teachers, "ФЕДОРОВ")] == [3]


def test_search_full_name_duplicates_and_limit() -> None:
    teachers = parse_teachers(RAW_TEACHERS)
    assert {t.id for t in search_teachers(teachers, "бойцова светлана")} == {4, 5}
    assert len(search_teachers(teachers, "адеев", limit=1)) == 1


def test_search_does_not_match_patronymic() -> None:
    teachers = parse_teachers(RAW_TEACHERS)
    assert search_teachers(teachers, "петров") == []


async def test_service_caches_groups() -> None:
    client = FakeEiosClient(["2026-2027"], _groups(), parse_teachers(RAW_TEACHERS))
    service = CatalogService(client, RedisCache(FakeRedis()))

    groups = await service.get_groups()
    assert len(groups) == 6
    assert client.calls["groups"] == 1

    await service.get_groups()  # второй раз — из кэша
    assert client.calls["groups"] == 1


async def test_service_hierarchy_and_search() -> None:
    service = _service()

    faculties = await service.get_faculties()
    assert {f.name for f in faculties} == {"АСП", "ИВИТШ", "ИУЭФ"}
    assert await service.get_levels(36) == [
        EducationLevel.BACHELOR,
        EducationLevel.MAGISTRACY,
    ]
    assert await service.get_courses(36, EducationLevel.BACHELOR) == [1, 2]
    assert [
        g.name for g in await service.get_groups_for(36, EducationLevel.BACHELOR, 1)
    ] == ["26-ПМбо-1", "26-ПМбо-2"]

    assert [t.id for t in await service.search_teachers("федоров")] == [3]
