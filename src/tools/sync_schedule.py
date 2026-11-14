"""CLI синхронизации расписания ЭИОС в локальную SQLite.

Примеры::

    python -m src.tools.sync_schedule --year 2026-2027 --all-groups
    python -m src.tools.sync_schedule --registered-only
    python -m src.tools.sync_schedule --stats
    python -m src.tools.sync_schedule --group-id 8948 --teacher-id 14

Бот не обращается к ЭИОС: эта команда — единственное место, где выполняются
сетевые запросы. Запускайте её там, где сайт доступен (или через прокси), и
переносите готовый файл ``data/bot.db`` на сервер бота.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from typing import Sequence

from src.api.eios_client import EiosApiError, EiosClient
from src.config import get_settings
from src.db.repository import UserRepository
from src.db.schedule_repository import ScheduleRepository, SyncStats
from src.db.sqlite_db import SqliteDatabase
from src.services.schedule_service import Role
from src.services.sync_service import ScheduleSyncService, SyncReport

logger = logging.getLogger("sync_schedule")


def build_parser() -> argparse.ArgumentParser:
    """Собирает парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        prog="python -m src.tools.sync_schedule",
        description=(
            "Скачивает расписание семестра из ЭИОС КГУ в локальную SQLite "
            "(data/bot.db)."
        ),
    )
    parser.add_argument("--year", help="учебный год, например 2026-2027")
    parser.add_argument(
        "--all-groups", action="store_true", help="все группы из справочника"
    )
    parser.add_argument(
        "--all-teachers",
        action="store_true",
        help="все преподаватели из справочника",
    )
    parser.add_argument(
        "--teachers-with-lessons",
        action="store_true",
        help="преподаватели, у которых есть пары в загруженных группах",
    )
    parser.add_argument(
        "--teachers-from-groups",
        action="store_true",
        help="построить расписание преподавателей из занятий групп (без ЭИОС)",
    )
    parser.add_argument(
        "--group-id",
        type=int,
        action="append",
        default=[],
        help="id группы (можно повторять)",
    )
    parser.add_argument(
        "--teacher-id",
        type=int,
        action="append",
        default=[],
        help="id преподавателя (можно повторять)",
    )
    parser.add_argument(
        "--registered-only",
        action="store_true",
        help="только цели из профилей зарегистрированных пользователей",
    )
    parser.add_argument(
        "--skip-catalog",
        action="store_true",
        help="не обновлять справочники (использовать уже сохранённые)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="игнорировать минимальный интервал между синхронизациями",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="одновременных запросов к ЭИОС (по умолчанию из настроек)",
    )
    parser.add_argument("--db", help="путь к файлу SQLite (по умолчанию из настроек)")
    parser.add_argument(
        "--stats", action="store_true", help="показать статистику локальной базы"
    )
    parser.add_argument(
        "--list-targets",
        action="store_true",
        help="показать синхронизированные цели",
    )
    return parser


async def _registered_targets(
    user_repository: UserRepository,
) -> list[tuple[Role, int]]:
    """Собирает цели из профилей пользователей в локальной SQLite."""
    users = await user_repository.list_all()

    targets: set[tuple[Role, int]] = set()
    for profile in users:
        if profile.role == Role.STUDENT.value and profile.group_id:
            targets.add((Role.STUDENT, profile.group_id))
        elif profile.role == Role.TEACHER.value and profile.teacher_id:
            targets.add((Role.TEACHER, profile.teacher_id))
    return sorted(targets, key=lambda item: (item[0].value, item[1]))


def _format_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


def _print_stats(database: SqliteDatabase, stats: SyncStats) -> None:
    print(f"Файл БД:            {database.path}")
    print(f"Занятий:            {stats.lessons}")
    print(f"Целей:              {stats.targets}")
    print(f"Семестров:          {stats.semesters}")
    print(f"Групп:              {stats.groups}")
    print(f"Преподавателей:     {stats.teachers}")
    print(f"Последний sync:     {_format_dt(stats.last_sync)}")
    print(f"Справочники:        {_format_dt(stats.last_catalog_sync)}")


def _print_targets(rows: Sequence[dict]) -> None:
    if not rows:
        print("Синхронизированных целей нет.")
        return
    header = (
        f"{'роль':8} {'id':>7}  {'название':32} {'год/сем':16} "
        f"{'статус':7} {'пар':>5}  покрытие"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        name = (row["name"] or "")[:32]
        semester = f"{row['academic_year'] or '—'}/{row['semester'] or '—'}"
        coverage = (
            f"{row['covered_from']}..{row['covered_to']}"
            if row["covered_from"] and row["covered_to"]
            else "—"
        )
        print(
            f"{row['role']:8} {row['target_id']:>7}  {name:32} "
            f"{semester:16} {row['status']:7} {row['lessons_count']:>5}  "
            f"{coverage}"
        )


async def run(args: argparse.Namespace) -> int:
    """Выполняет команду и возвращает код завершения."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    schedule_db = SqliteDatabase(
        args.db or settings.sqlite_db_path, echo=settings.db_echo
    )
    await schedule_db.create_schema()
    repository = ScheduleRepository(schedule_db.session_factory)
    user_repository = UserRepository(schedule_db.session_factory)

    try:
        if args.stats:
            _print_stats(schedule_db, await repository.stats())
            return 0
        if args.list_targets:
            _print_targets(await repository.list_targets())
            return 0

        targets: list[tuple[Role, int]] = []
        if args.registered_only:
            try:
                targets = await _registered_targets(user_repository)
            except Exception as exc:  # noqa: BLE001 - понятное сообщение в CLI
                print(
                    f"Не удалось прочитать профили пользователей: {exc}",
                    file=sys.stderr,
                )
                return 2
            if not targets:
                logger.warning("Зарегистрированных пользователей с профилем нет")

        no_targets = not any(
            (
                args.all_groups,
                args.all_teachers,
                args.teachers_with_lessons,
                args.teachers_from_groups,
                args.group_id,
                args.teacher_id,
                targets,
            )
        )

        async with EiosClient(
            settings.eios_base_url,
            proxy=settings.eios_proxy_url or None,
            user_agent=settings.eios_user_agent or None,
            browser_headers=settings.eios_browser_headers,
            retries=4,
            backoff=3.0,
        ) as client:
            service = ScheduleSyncService(
                client,
                repository,
                concurrency=(
                    args.concurrency
                    if args.concurrency is not None
                    else settings.schedule_sync_concurrency
                ),
                min_interval=settings.schedule_sync_min_interval,
                request_delay=settings.schedule_sync_request_delay,
                timezone=settings.timezone,
            )

            year = args.year
            if not args.skip_catalog:
                year = await service.sync_catalog(year)

            report = SyncReport()
            if args.all_groups:
                report.merge(
                    await service.sync_groups(year=year, force=args.force)
                )
            if args.group_id:
                report.merge(
                    await service.sync_groups(ids=args.group_id, force=args.force)
                )
            if args.all_teachers:
                report.merge(await service.sync_teachers(force=args.force))
            if args.teachers_with_lessons:
                teacher_ids = await repository.teacher_ids_with_lessons()
                report.merge(
                    await service.sync_teachers(ids=teacher_ids, force=args.force)
                )
            if args.teachers_from_groups:
                derived = await repository.rebuild_teacher_schedules()
                print(f"Расписание преподавателей из групп: {derived} пар")
            if args.teacher_id:
                report.merge(
                    await service.sync_teachers(
                        ids=args.teacher_id, force=args.force
                    )
                )
            if targets:
                report.merge(
                    await service.sync_targets(targets, force=args.force)
                )

        if no_targets:
            print(
                "Справочники обновлены. Чтобы загрузить расписание, укажите "
                "--all-groups, --group-id, --teacher-id, --all-teachers "
                "или --registered-only."
            )
            return 0

        print(f"Синхронизация завершена: {report.summary()}")
        for error in report.errors:
            print(f"  ошибка: {error}", file=sys.stderr)
        return 0 if report.failed == 0 else 1
    finally:
        await schedule_db.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI. Возвращает код завершения процесса."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except EiosApiError as exc:
        print(f"ЭИОС недоступен: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

