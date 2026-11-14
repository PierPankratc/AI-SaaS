#!/usr/bin/env bash
# Восстановление базы SQLite в Docker-volume `bot_data`.
#
# Бот читает базу не из ~/app/data, а из тома, смонтированного в /app/data
# (см. docker-compose.yml). Этот скрипт кладёт файл именно туда, выставляет
# владельца appuser (uid 10001) и перезапускает контейнер.
#
# Использование (на сервере, из каталога ~/app):
#   bash scripts/restore_db.sh                  # взять ./data/bot.db
#   bash scripts/restore_db.sh ./bot.db         # взять другой файл
#   bash scripts/restore_db.sh --from-git       # взять data/bot.db из origin/main
#
# Переменные окружения:
#   SERVICE   имя сервиса в compose (по умолчанию bot)
#   DB_UID    uid пользователя бота (по умолчанию 10001)
#   COMPOSE   команда compose (по умолчанию "docker compose")
set -euo pipefail

SERVICE="${SERVICE:-bot}"
DB_UID="${DB_UID:-10001}"
COMPOSE="${COMPOSE:-docker compose}"

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
}

SOURCE="data/bot.db"
FROM_GIT=0

case "${1:-}" in
  -h | --help)
    usage
    exit 0
    ;;
  --from-git)
    FROM_GIT=1
    ;;
  "")
    ;;
  *)
    SOURCE="$1"
    ;;
esac

if [[ "$FROM_GIT" == "1" ]]; then
  echo "==> Беру data/bot.db из origin/main"
  git fetch origin main
  mkdir -p data
  git show origin/main:data/bot.db > data/bot.db
  SOURCE="data/bot.db"
fi

if [[ ! -f "$SOURCE" ]]; then
  echo "ОШИБКА: файл базы не найден: $SOURCE" >&2
  echo "  • положите файл на сервер: scp data/bot.db deploy@SERVER:~/app/bot.db" >&2
  echo "  • или восстановите из репозитория: bash scripts/restore_db.sh --from-git" >&2
  exit 1
fi

if [[ "$(head -c 15 "$SOURCE")" != "SQLite format 3" ]]; then
  echo "ОШИБКА: $SOURCE не является SQLite-базой (нет заголовка 'SQLite format 3')" >&2
  exit 1
fi

SOURCE_ABS="$(cd "$(dirname "$SOURCE")" && pwd)/$(basename "$SOURCE")"
SIZE="$(du -h "$SOURCE_ABS" | cut -f1)"
echo "==> Файл базы: $SOURCE_ABS ($SIZE)"

echo "==> Останавливаю контейнер $SERVICE"
$COMPOSE stop "$SERVICE" >/dev/null

echo "==> Копирую базу в /app/data и выставляю права"
$COMPOSE run --rm --no-deps --user root \
  -v "${SOURCE_ABS}:/restore/bot.db:ro" \
  --entrypoint sh "$SERVICE" -c "
    set -e
    rm -f /app/data/bot.db-wal /app/data/bot.db-shm
    cp /restore/bot.db /app/data/bot.db
    chown -R ${DB_UID}:${DB_UID} /app/data
    chmod 755 /app/data
    chmod 644 /app/data/bot.db
    ls -l /app/data
  "

echo "==> Запускаю контейнер $SERVICE"
$COMPOSE up -d "$SERVICE"
sleep 3

echo "==> Проверка содержимого базы"
if ! $COMPOSE exec -T "$SERVICE" python -m src.tools.sync_schedule --stats; then
  echo "ОШИБКА: база не читается. Логи: $COMPOSE logs --tail=100 $SERVICE" >&2
  exit 1
fi

echo "==> Готово: логи — $COMPOSE logs -f --tail=50 $SERVICE"
