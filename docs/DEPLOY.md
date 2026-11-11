# Деплой на VPS, мониторинг и логирование

Пошаговая инструкция для Ubuntu 22.04/24.04.

## 1. Подготовка сервера

```bash
ssh root@SERVER_IP

adduser deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy

ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

Рекомендуется отключить вход root по паролю и оставить только SSH-ключи
(`/etc/ssh/sshd_config`: `PermitRootLogin no`, `PasswordAuthentication no`).

## 2. Установка Docker

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker deploy
```

Перезайдите под `deploy`, чтобы применилась группа `docker`.

## 3. Код и конфигурация

```bash
su - deploy
git clone <URL_РЕПОЗИТОРИЯ> app && cd app
cp .env.example .env
chmod 600 .env
nano .env
```

В `.env` обязательно задайте:

```
VK_BOT_TOKEN=<новый токен сообщества>
POSTGRES_PASSWORD=<длинный случайный пароль>
ENVIRONMENT=production
SENTRY_DSN=<dsn sentry или пусто>
HEARTBEAT_URL=<ping url healthchecks.io или пусто>
```

Сгенерировать пароль: `openssl rand -base64 24`.

> `.env` не должен попадать в git. Порты БД/Redis в compose открыты только на
> `127.0.0.1` и наружу не публикуются.

## 4. Запуск

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f bot
```

Ожидаемые строки в логах: «Кэш справочников ЭИОС прогрет» и
«Запуск бота ВК (long poll)…». Таблица `users` создаётся автоматически.

## 5. Обновление версии

```bash
git pull
docker compose up -d --build
docker image prune -f
```

## 6. Логирование

- Просмотр: `docker compose logs -f --tail=200 bot`.
- Ротация уже включена в compose (`json-file`, 10 МБ × 3 файла).
- Для централизованных логов можно сменить драйвер на `journald`:
  ```yaml
  logging:
    driver: journald
  ```
  и смотреть `journalctl -u docker -f`.

## 7. Мониторинг

### 7.1 Sentry — ошибки приложения

1. Зарегистрируйтесь на sentry.io, создайте проект **Python**.
2. Скопируйте DSN в `.env`: `SENTRY_DSN=...`, `ENVIRONMENT=production`.
3. Перезапустите: `docker compose up -d`.

Исключения из обработчиков и фоновых задач попадут в Sentry (персональные
данные не отправляются).

### 7.2 Heartbeat — «бот жив?»

Long-poll бот не слушает порт, поэтому внешний аптайм-монитор его «не видит».
Используйте push-мониторинг («мёртвая рука»):

1. healthchecks.io → **New Check** → Period = 5 минут.
2. Скопируйте ping URL в `.env`: `HEARTBEAT_URL=https://hc-ping.com/...`,
   `HEARTBEAT_INTERVAL=300`.
3. Настройте уведомления (email/Telegram). Пропуск пингов → алерт.

### 7.3 Uptime Kuma — сервер и контейнеры

Разверните Uptime Kuma (можно на этом же VPS) и добавьте монитор на IP сервера
(Ping/HTTP). По желанию — Docker-монитор контейнера `bot`.

### 7.4 (опционально) Prometheus + Grafana + Loki

Для «взрослого» стека: метрики через `prometheus-client`, логи через
Promtail → Loki, дашборды в Grafana. Для одного бота обычно достаточно
Sentry + healthchecks.io.

## 8. Бэкапы PostgreSQL

```bash
# ручной бэкап
docker compose exec -T postgres pg_dump -U postgres vk_schedule \
  | gzip > backup_$(date +%F).sql.gz

# cron (ежедневно в 3:00)
0 3 * * * cd /home/deploy/app && \
  docker compose exec -T postgres pg_dump -U postgres vk_schedule \
  | gzip > /home/deploy/backup_$(date +\%F).sql.gz
```

Храните бэкапы вне сервера (S3/другой хост). Восстановление:

```bash
gunzip -c backup.sql.gz | docker compose exec -T postgres psql -U postgres -d vk_schedule
```

## 9. Безопасность

- Не публикуйте порты 5432/6379 наружу (в compose — только `127.0.0.1`).
- Сильные пароли, `.env` с правами `600`, вход по SSH-ключам, без root-логина.
- `ufw` + fail2ban, регулярные обновления: `sudo apt upgrade`.
- Перевыпускайте токен ВК при подозрении на утечку.
- `docker compose config` раскрывает значения переменных — не публикуйте его вывод.

## 10. Диагностика

```bash
docker compose ps
docker compose logs --tail=200 bot
docker compose exec redis redis-cli ping
docker compose exec postgres pg_isready -U postgres
```

