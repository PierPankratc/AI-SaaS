"""Тесты репозитория профилей (единый SQLite-файл, без внешней БД)."""


import pytest
import pytest_asyncio
from pydantic import ValidationError

from src.db.sqlite_db import SqliteDatabase
from src.db.repository import UserProfile, UserRepository


@pytest_asyncio.fixture
async def repo(tmp_path):
    database = SqliteDatabase(tmp_path / "test.db")
    await database.create_schema()
    try:
        yield UserRepository(database.session_factory)
    finally:
        await database.dispose()


async def test_save_and_get_student(repo: UserRepository) -> None:
    profile = UserProfile(
        vk_id=1,
        role="student",
        faculty_id=36,
        faculty_name="ИВИТШ",
        edu_level="bachelor",
        course=1,
        group_id=8948,
        group_name="26-ПМбо-1",
    )

    await repo.save(profile)

    assert await repo.get(1) == profile


async def test_update_overwrites_profile(repo: UserRepository) -> None:
    await repo.save(
        UserProfile(vk_id=1, role="student", group_id=8948, group_name="26-ПМбо-1")
    )
    await repo.save(
        UserProfile(
            vk_id=1, role="teacher", teacher_id=14, teacher_name="Адеева Т. Н."
        )
    )

    loaded = await repo.get(1)
    assert loaded is not None
    assert loaded.role == "teacher"
    assert loaded.teacher_id == 14
    assert loaded.group_id is None


async def test_reset_removes_profile(repo: UserRepository) -> None:
    await repo.save(UserProfile(vk_id=5, role="teacher", teacher_id=1))
    await repo.reset(5)
    assert await repo.get(5) is None


async def test_sql_injection_payload_is_stored_literally(repo: UserRepository) -> None:
    malicious = "'; DROP TABLE users; --"
    await repo.save(
        UserProfile(vk_id=7, role="teacher", teacher_id=1, teacher_name=malicious)
    )

    loaded = await repo.get(7)
    assert loaded is not None
    assert loaded.teacher_name == malicious
    # Таблица не удалена: следующий запрос работает.
    assert await repo.get(7) is not None


def test_invalid_role_rejected() -> None:
    with pytest.raises(ValidationError):
        UserProfile(vk_id=1, role="admin")


def test_invalid_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        UserProfile(vk_id=0, role="student")
    with pytest.raises(ValidationError):
        UserProfile(vk_id=1, role="student", group_id=0)
    with pytest.raises(ValidationError):
        UserProfile(vk_id=1, role="student", course=9)


async def test_list_all_returns_profiles(repo: UserRepository) -> None:
    await repo.save(UserProfile(vk_id=2, role="student", group_id=1))
    await repo.save(UserProfile(vk_id=1, role="teacher", teacher_id=2))

    profiles = await repo.list_all()

    assert [profile.vk_id for profile in profiles] == [1, 2]

