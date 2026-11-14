"""Единая локальная база данных приложения (SQLite).

Бот работает полностью офлайн: профили, черновики регистрации, справочники
и расписание семестра лежат в одном файле (по умолчанию ``data/bot.db``).
ЭИОС вызывается только CLI-синхронизацией, а не процессом бота.

Используются два независимых набора ORM-моделей (профили и расписание),
поэтому ``create_schema`` создаёт обе схемы в одном соединении.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from src.db.models import Base
from src.db.schedule_models import ScheduleBase

# ``src/db/sqlite_db.py`` → ``src/db`` → ``src`` → корень проекта.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = "data/bot.db"
MEMORY_PATH = ":memory:"


def resolve_db_path(path: str | Path) -> Path:
    """Приводит путь к файлу БД к абсолютному.

    Относительные пути (по умолчанию ``data/bot.db``) разрешаются от корня
    проекта, а не от текущего каталога процесса, — база всегда лежит «внутри
    проекта», откуда бы ни запускали команду.
    """
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return (PROJECT_ROOT / candidate).resolve()


def build_db_url(path: str | Path) -> str:
    """Собирает SQLAlchemy-URL для файла SQLite (или in-memory базы)."""
    if str(path) == MEMORY_PATH:
        return "sqlite+aiosqlite:///:memory:"
    return f"sqlite+aiosqlite:///{resolve_db_path(path).as_posix()}"


def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
    """Выставляет PRAGMA для каждого нового соединения."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


class SqliteDatabase:
    """Владелец асинхронного движка локальной SQLite-базы."""

    def __init__(
        self,
        path: str | Path = DEFAULT_DB_PATH,
        *,
        echo: bool = False,
    ) -> None:
        self.path: Path | str = (
            MEMORY_PATH if str(path) == MEMORY_PATH else resolve_db_path(path)
        )
        self.url = build_db_url(path)

        kwargs: dict[str, Any] = {"echo": echo}
        if self.url.endswith(":memory:"):
            kwargs["poolclass"] = StaticPool
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            assert isinstance(self.path, Path)
            self.path.parent.mkdir(parents=True, exist_ok=True)

        self.engine: AsyncEngine = create_async_engine(self.url, **kwargs)
        event.listen(self.engine.sync_engine, "connect", _configure_sqlite)
        self.session_factory: async_sessionmaker[AsyncSession] = (
            async_sessionmaker(self.engine, expire_on_commit=False)
        )

    async def create_schema(self) -> None:
        """Создаёт все таблицы приложения, если их нет (идемпотентно)."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.run_sync(ScheduleBase.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
