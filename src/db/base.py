"""Подключение к базе данных (PostgreSQL, async SQLAlchemy)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.db.models import Base


class Database:
    """Владелец асинхронного движка и фабрики сессий."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        kwargs: dict = {"echo": echo}
        if not url.startswith("sqlite"):
            kwargs["pool_pre_ping"] = True

        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def create_schema(self) -> None:
        """Создаёт таблицы, если их нет (идемпотентно)."""
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
