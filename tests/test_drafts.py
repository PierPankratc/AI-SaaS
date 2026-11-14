"""Тесты черновиков регистрации: SQLite-хранилище и TTL."""

import pytest_asyncio

from src.bot.drafts import InMemoryDrafts, OnboardingDraft, SqliteDrafts
from src.db.sqlite_db import SqliteDatabase


@pytest_asyncio.fixture
async def database(tmp_path):
    db = SqliteDatabase(tmp_path / "bot.db")
    await db.create_schema()
    try:
        yield db
    finally:
        await db.dispose()


async def test_sqlite_save_get_update_clear(database) -> None:
    drafts = SqliteDrafts(database.session_factory)

    first = OnboardingDraft(role="student", faculty_id=36, page=1)
    await drafts.save(1, first)
    assert await drafts.get(1) == first

    second = OnboardingDraft(role="teacher", teacher_query="адеев", page=2)
    await drafts.save(1, second)
    assert await drafts.get(1) == second

    await drafts.clear(1)
    assert await drafts.get(1) is None


async def test_sqlite_drafts_survive_new_instance(database) -> None:
    await SqliteDrafts(database.session_factory).save(
        5, OnboardingDraft(role="student", course=2)
    )

    restored = await SqliteDrafts(database.session_factory).get(5)

    assert restored is not None
    assert restored.course == 2


async def test_sqlite_drafts_expire_by_ttl(database) -> None:
    now = [100.0]
    drafts = SqliteDrafts(
        database.session_factory, ttl=10, clock=lambda: now[0]
    )
    await drafts.save(1, OnboardingDraft(role="student"))

    now[0] = 111.0
    assert await drafts.get(1) is None

    # Просроченная строка удалена из БД.
    renewed = SqliteDrafts(database.session_factory, ttl=10)
    assert await renewed.get(1) is None


async def test_in_memory_drafts_expire() -> None:
    now = [0.0]
    drafts = InMemoryDrafts(ttl=5, clock=lambda: now[0])
    await drafts.save(1, OnboardingDraft(role="teacher"))

    assert await drafts.get(1) is not None
    now[0] = 6.0
    assert await drafts.get(1) is None
