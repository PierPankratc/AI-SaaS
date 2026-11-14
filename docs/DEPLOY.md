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

## 3. Код и конфигурация (приватный GitHub-репозиторий)

Репозиторий приватный, поэтому клонируем по SSH через **deploy key**
(доступ только на чтение и только к этому репозиторию):

```bash
su - deploy
ssh-keygen -t ed25519 -C "vps-aissas-deploy" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Скопируйте вывод и добавьте ключ в GitHub:
**Repository → Settings → Deploy keys → Add deploy key**
(галочку «Allow write access» оставьте выключенной). Проверка и клон:

```bash
ssh -T git@github.com          # ожидаемо: "successfully authenticated"
git clone git@github.com:PierPankratc/AI-SaaS.git app
cd app
```

Альтернатива по HTTPS (если SSH недоступен) — personal access token с правом `repo`:
```bash
git clone https://<LOGIN>:<TOKEN>@github.com/PierPankratc/AI-SaaS.git app
```
Не храните токен в истории команд — используйте credential helper.

Затем `.env`:
```bash
cp .env.example .env
chmod 600 .env
nano .env
```

В `.env` обязательно задайте:

```
VK_BOT_TOKEN=<новый токен сообщества>
ENVIRONMENT=production
SENTRY_DSN=<dsn sentry или пусто>
HEARTBEAT_URL=<ping url healthchecks.io или пусто>
```

Опционально: `SQLITE_DB_PATH=/app/data/bot.db` (значение по умолчанию в compose),
`EIOS_PROXY_URL=...` — только для CLI-синхронизации.

> `.env` не должен попадать в git. Вся информация хранится в одной SQLite-базе
> `data/bot.db` (volume `bot_data`); PostgreSQL и Redis больше не используются.


## 4. Запуск

Сначала подготовьте базу с расписанием. Вариант A — сервер имеет доступ к ЭИОС
(напрямую или через `EIOS_PROXY_URL`):

```bash
docker compose up -d --build
docker compose exec bot python -m src.tools.sync_schedule --all-groups
docker compose exec bot python -m src.tools.sync_schedule --skip-catalog --teachers-from-groups
docker compose logs -f bot
```

Вариант B — сервер **не имеет доступа** к ЭИОС (рекомендуется для заблокированных
IP): соберите `data/bot.db` на машине с доступом к сайту и перенесите файл.

```bash
# на локальной машине
python -m src.tools.sync_schedule --year 2026-2027 --all-groups
python -m src.tools.sync_schedule --skip-catalog --teachers-from-groups
scp data/bot.db deploy@SERVER:~/app/data/bot.db

# на сервере
cd ~/app
docker compose up -d --build
docker compose cp data/bot.db bot:/app/data/bot.db
docker compose exec -u root bot chown 10001:10001 /app/data/bot.db
docker compose restart bot
docker compose logs -f bot
```

При обновлениях не перезаписывайте боевую базу «с нуля» — сначала заберите её
с профилями, обновите расписание и верните обратно:

```bash
docker compose cp bot:/app/data/bot.db ./bot.db
python -m src.tools.sync_schedule --db ./bot.db --all-groups --force
python -m src.tools.sync_schedule --db ./bot.db --skip-catalog --teachers-from-groups
docker compose cp ./bot.db bot:/app/data/bot.db
docker compose exec -u root bot chown 10001:10001 /app/data/bot.db
docker compose restart bot
```

Ожидаемые строки в логах: «Локальная база: N пар, M целей…» (или предупреждение
о пустой базе) и «Запуск бота ВК (long poll, офлайн-режим)…». Таблицы создаются
автоматически при старте.

> ЭИОС ограничивает частоту запросов (~30/мин). Для первой массовой загрузки
> используйте `--concurrency 1` и `SCHEDULE_SYNC_REQUEST_DELAY=3.0`; клиент
> сам повторяет `429` с учётом `Retry-After`. Повторный запуск продолжит
> с места остановки: успешные и пустые цели пропускаются.



## 5. Обновление версии

```bash
cd ~/app
git fetch origin main
git reset --hard origin/main
docker compose up -d --build --remove-orphans
docker image prune -f
docker compose logs -f --tail=100 bot
```

`--build` обязателен: без пересборки контейнер продолжит работать со старым
образом. `--remove-orphans` удаляет контейнеры сервисов, которых больше нет
в `docker-compose.yml` (например, старый `postgres`).

Проверить, какой код реально работает в контейнере:

```bash
docker compose exec bot ls /app/src/db
# актуальная версия: models.py repository.py schedule_models.py
# schedule_repository.py sqlite_db.py  (файла base.py быть не должно)
docker compose exec bot python -m src.tools.sync_schedule --stats
```

Автодеплой: workflow `.github/workflows/deploy.yml` применяется при push в
`main` (и вручную через «Run workflow»). Он выполняет те же команды на сервере.

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

## 8. Бэкапы SQLite

В `data/bot.db` (volume `bot_data`) лежит всё: профили пользователей, черновики
и расписание семестра.

```bash
# копия «живой» базы через SQLite backup API (без остановки бота)
docker compose exec bot python -c "import sqlite3; sqlite3.connect('/app/data/bot.db').backup(sqlite3.connect('/app/data/backup.db'))"
docker compose cp bot:/app/data/backup.db ./bot_backup_$(date +%F).db

# cron (ежедневно в 3:00)
0 3 * * * cd /home/deploy/app && \
  docker compose exec -T bot python -c "import sqlite3; sqlite3.connect('/app/data/bot.db').backup(sqlite3.connect('/app/data/backup.db'))" && \
  docker compose cp bot:/app/data/backup.db /home/deploy/bot_backup_$(date +\%F).db
```

Восстановление:

```bash
docker compose stop bot
docker compose cp ./bot_backup_2026-11-14.db bot:/app/data/bot.db
docker compose exec -u root bot chown 10001:10001 /app/data/bot.db
docker compose start bot
```

> При копировании файла вручную останавливайте бота: при включённом WAL рядом
> могут быть `bot.db-wal` и `bot.db-shm`.


## 9. Безопасность

- Наружу открыт только SSH (и HTTP/HTTPS, если понадобятся); база — локальный
  файл, сетевых портов у неё нет.
- `.env` с правами `600`, вход по SSH-ключам, без root-логина.
- `ufw` + fail2ban, регулярные обновления: `sudo apt upgrade`.
- Перевыпускайте токен ВК при подозрении на утечку.
- `data/bot.db` содержит персональные данные (VK ID, группа) — не публикуйте
  файл и храните бэкапы в защищённом месте.
- `docker compose config` раскрывает значения переменных — не публикуйте его вывод.

## 10. Диагностика

```bash
docker compose ps
docker compose logs --tail=200 bot
docker compose exec bot python -m src.tools.sync_schedule --stats
docker compose exec bot python -m src.tools.sync_schedule --list-targets
# проверка доступа к ЭИОС (только там, где запускается синхронизация)
docker compose exec bot python -m src.healthcheck
```

## 11. Если ЭИОС возвращает 451 (IP заблокирован)

`https://eios.kosgos.ru` может блокировать зарубежные/датацентровые IP — ответ
`451 Unavailable For Legal Reasons`. В логах это выглядит так:

```
451 <html>...
Не удалось загрузить справочники
```

Решения (важно: бот работает офлайн — проблема касается только синхронизации):

1. Запускать `python -m src.tools.sync_schedule ...` на машине **в РФ** и
   переносить `data/bot.db` на сервер.
2. Настроить прокси с российским **резидентным** IP и указать его в `.env`
   (используется CLI-синхронизацией):
   ```
   EIOS_PROXY_URL=http://user:pass@203.0.113.10:8080
   ```
   Поддерживаются `http://`, `https://` и `socks5://` — пакет `httpx[socks]`
   уже включён в зависимости. Датацентровые IP тоже могут блокироваться,
   поэтому лучше резидентный/мобильный прокси.
3. Поднять WireGuard/VPN с российским выходом на машине, где выполняется
   синхронизация.

Проверка после настройки:

```bash
docker compose up -d
docker compose exec bot python -m src.healthcheck
# ожидаемо: "OK: years=[...], groups=..., teachers=..."
```

Утилита `src.healthcheck` пробует 4 стратегии: UA бота / браузера × напрямую /
через прокси. По результату:

- `browser / direct` = OK → помогает маскировка заголовков: поставьте
  `EIOS_BROWSER_HEADERS=true`;
- OK только `*/proxy` → нужен прокси (заголовки IP не меняют);
- всё FAIL → блок по IP; заголовки не помогут, нужен резидентный прокси/другой IP.

## 12. Если `PermissionError: '/app/.vkbottle'` или не пишется `bot.db`

Пересоберите контейнер и проверьте права:

```bash
docker compose build --no-cache bot
docker compose up -d --force-recreate bot
docker compose exec bot ls -ld /app /app/.vkbottle /app/data
docker compose exec -u root bot chown -R 10001:10001 /app/data
docker compose restart bot
```

## 13. В логах `asyncpg` / `postgresql+asyncpg` / `Connect call failed ... 5432`

Это **не ошибка кода из `main`**, а старый образ: контейнер запущен с версией
до перехода на SQLite. Признаки в трейсбеке:

```
File "/app/src/db/base.py", line 30, in create_schema
...
File ".../sqlalchemy/dialects/postgresql/asyncpg.py", line 967, in connect
OSError: Multiple exceptions: [Errno 111] Connect call failed ('127.0.0.1', 5432)
```

В актуальной версии файла `src/db/base.py` нет — база открывается через
`src/db/sqlite_db.py` (`sqlite+aiosqlite`), PostgreSQL не нужен.

Исправление на сервере:

```bash
cd ~/app
git fetch origin main
git reset --hard origin/main
docker compose down --remove-orphans
docker compose build --no-cache bot
docker compose up -d --force-recreate
docker compose logs -f --tail=100 bot
# ожидаемо: «Локальная база: ...» и «Запуск бота ВК (long poll, офлайн-режим)…»
```

Автодеплой не срабатывал, пока workflow лежал в `.github/workflow/` (нужна
папка `.github/workflows/`). Сейчас путь исправлен: проверьте, что на GitHub
в разделе **Actions → Deploy** появляются запуски при push в `main`.

Если профили пользователей остались в старой PostgreSQL, их можно один раз
перенести в SQLite (см. раздел «Перенос профилей из старой PostgreSQL» в
`README.md`). После переноса старый контейнер и его volume можно удалить:
`docker compose down --remove-orphans` и `docker volume ls` / `docker volume rm`.



