#!/usr/bin/env bash
# Сторож контейнера бота: перезапускает его, если контейнер остановлен,
# упал или помечен unhealthy (бот завис — файл-«пульс» не обновляется).
#
# Запускается на сервере по расписанию (systemd timer или cron).
# SSH-сессия для этого не нужна.
#
# Использование:
#   APP_DIR=/home/deploy/app bash scripts/watchdog.sh
#
# Переменные окружения:
#   APP_DIR   каталог проекта (по умолчанию текущий каталог)
#   SERVICE   имя сервиса в compose (по умолчанию bot)
#   DOCKER    команда docker (по умолчанию docker)
set -euo pipefail

APP_DIR="${APP_DIR:-$(pwd)}"
SERVICE="${SERVICE:-bot}"
DOCKER="${DOCKER:-docker}"
COMPOSE="${COMPOSE:-$DOCKER compose}"

cd "$APP_DIR"

CONTAINER_ID="$($COMPOSE ps -q "$SERVICE" 2>/dev/null || true)"

if [[ -z "$CONTAINER_ID" ]]; then
  echo "[watchdog] контейнер $SERVICE не найден — запускаю"
  $COMPOSE up -d "$SERVICE"
  exit 0
fi

STATE="$($DOCKER inspect --format '{{.State.Status}}' "$CONTAINER_ID")"
HEALTH="$($DOCKER inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER_ID")"

if [[ "$STATE" != "running" ]]; then
  echo "[watchdog] $SERVICE state=$STATE — поднимаю"
  $COMPOSE up -d "$SERVICE"
elif [[ "$HEALTH" == "unhealthy" ]]; then
  echo "[watchdog] $SERVICE unhealthy — перезапускаю"
  $COMPOSE restart "$SERVICE"
fi
