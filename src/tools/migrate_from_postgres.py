"""Одноразовый перенос профилей из старой PostgreSQL в локальную SQLite.

Нужен только при переезде с прежней версии бота. После миграции PostgreSQL
не требуется.

    pip install asyncpg
    python -m src.tools.migrate_from_postgres \
        --source postgresql://postgres:postgres@localhost:5432/vk_schedule \
        --target data/bot.db
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Sequence

from src.config import get_settings
from src.db.repository import UserProfile, UserRepository
from src.db.sqlite_db import SqliteDatabase

COLUMNS = (
    "vk_id",
    "role",
    "faculty_id",
    "faculty_name",
    "edu_level",
    "course",
    "group_id",
    "group_name",
    "teacher_id",
    "teacher_name",
)


async def save_profiles(
    repository: UserRepository, rows: Sequence[dict[str, Any]]
) -> int:
    """Сохраняет строки старой таблицы ``users`` в SQLite."""
    saved = 0
    for row in rows:
        profile = UserProfile(**{key: row[key] for key in COLUMNS})
        await repository.save(profile)
        saved += 1
    return saved


async def migrate(source: str, target: str) -> int:
    """Читает профили из PostgreSQL и записывает их в SQLite."""
    try:
        import asyncpg
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise SystemExit(
            "Для миграции установите asyncpg: python -m pip install asyncpg"
        ) from exc

    connection = await asyncpg.connect(source)
    try:
        rows = await connection.fetch(
            f"SELECT {', '.join(COLUMNS)} FROM users ORDER BY vk_id"
        )
    finally:
        await connection.close()

    database = SqliteDatabase(target)
    await database.create_schema()
    try:
        repository = UserRepository(database.session_factory)
        saved = await save_profiles(repository, [dict(row) for row in rows])
    finally:
        await database.dispose()
    return saved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.migrate_from_postgres",
        description="Переносит профили пользователей из PostgreSQL в SQLite.",
    )
    parser.add_argument(
        "--source",
        help="DSN старой PostgreSQL (по умолчанию переменная окружения DATABASE_URL)",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="файл SQLite (по умолчанию SQLITE_DB_PATH из настроек)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    settings = get_settings()
    source = args.source or os.environ.get("DATABASE_URL", "")
    if not source:
        print(
            "Укажите --source (DSN PostgreSQL): переменная DATABASE_URL не задана.",
            file=sys.stderr,
        )
        return 2

    try:
        saved = asyncio.run(migrate(source, args.target or settings.sqlite_db_path))
    except Exception as exc:  # noqa: BLE001 - понятное сообщение в CLI
        print(f"Не удалось перенести профили: {exc}", file=sys.stderr)
        return 1

    print(f"Перенесено профилей: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
