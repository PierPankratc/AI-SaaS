"""Тест переноса профилей из старой PostgreSQL в SQLite (без asyncpg)."""

import pytest_asyncio

from src.db.repository import UserRepository
from src.db.sqlite_db import SqliteDatabase
from src.tools.migrate_from_postgres import save_profiles


@pytest_asyncio.fixture
async def repository(tmp_path):
    database = SqliteDatabase(tmp_path / "bot.db")
    await database.create_schema()
    try:
        yield UserRepository(database.session_factory)
    finally:
        await database.dispose()


async def test_save_profiles_maps_all_columns(repository) -> None:
    rows = [
        {
            "vk_id": 1,
            "role": "student",
            "faculty_id": 36,
            "faculty_name": "ИВИТШ",
            "edu_level": "bachelor",
            "course": 1,
            "group_id": 8948,
            "group_name": "26-ПМбо-1",
            "teacher_id": None,
            "teacher_name": None,
        },
        {
            "vk_id": 2,
            "role": "teacher",
            "faculty_id": None,
            "faculty_name": None,
            "edu_level": None,
            "course": None,
            "group_id": None,
            "group_name": None,
            "teacher_id": 14,
            "teacher_name": "Адеева Т. Н.",
        },
    ]

    assert await save_profiles(repository, rows) == 2

    student = await repository.get(1)
    assert student is not None
    assert student.group_id == 8948
    assert student.group_name == "26-ПМбо-1"

    teacher = await repository.get(2)
    assert teacher is not None
    assert teacher.teacher_id == 14
    assert teacher.group_id is None
