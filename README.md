# VK-бот расписания КГУ (полностью офлайн)

Бот ВКонтакте для просмотра расписания Костромского государственного
университета. **Вся информация хранится локально в одном файле SQLite**
(`data/bot.db`): профили пользователей, черновики регистрации, справочники и
расписание семестра.

Бот **не обращается к сайту ЭИОС** — он работает даже там, где доступ к
`eios.kosgos.ru` заблокирован. Данные скачивает отдельная CLI-команда на
машине с доступом к ЭИОС (или через прокси), после чего файл базы переносится
на сервер бота.

## Возможности

- **Регистрация один раз** (сохраняется в SQLite):
  роль → институт → ступень обучения → курс → группа.
  Ступени: бакалавриат, специалитет, магистратура, аспирантура, СПО.
- **Преподаватель**: вводит фамилию текстом, затем выбирает себя из найденных
  однофамильцев кнопками.
- **Главное меню кнопками**: «На сегодня», «На завтра», «На эту неделю»,
  «Сменить группу / профиль».
- **Офлайн-режим**: ни одного сетевого запроса, кроме Long Poll ВКонтакте.
- **Синхронизация семестра**: один запрос к ЭИОС на группу/преподавателя
  отдаёт весь семестр; данные идемпотентно заменяются в SQLite.
- **Безопасность**: антифлуд, валидация ввода, строгий разбор payload кнопок,
  защита от SQL-инъекций через ORM, безопасные ошибки.

## Архитектура

```
┌─────────────────────────── машина с доступом к ЭИОС ──────────────────────────┐
│ python -m src.tools.sync_schedule --all-groups                              │
│                 │  httpx                                                     │
│                 ▼                                                            │
│          EiosClient ──► расписание семестра ──┐                              │
└───────────────────────────────────────────────┼──────────────────────────────┘
                                                ▼
                                  ┌────────────────────────────┐
                                  │ data/bot.db (SQLite)       │
                                  │ users / drafts / groups /  │
                                  │ teachers / semesters /     │
                                  │ lessons / targets / …      │
                                  └─────────────┬──────────────┘
                                                │ только чтение
VK (long poll) ──► BotCore ──► CatalogService ──┤
                     ├─► ScheduleService ───────┘
                     ├─► UserRepository
                     ├─► SqliteDrafts
                     └─► RateLimiter (в памяти)
```

Внешних серверов нет: не нужны PostgreSQL и Redis. ЭИОС вызывает только CLI.

## Структура проекта

```
src/
├── config.py                 # настройки (.env, pydantic-settings)
├── main.py                   # запуск: SQLite + long poll
├── monitoring.py             # Sentry + heartbeat
├── healthcheck.py            # диагностика доступа к ЭИОС (для синхронизации)
├── api/
│   ├── schemas.py            # модели API и классификация ступеней
│   └── eios_client.py        # httpx-клиент ЭИОС (только для CLI)
├── db/
│   ├── models.py             # ORM: User, Draft
│   ├── sqlite_db.py          # единый движок SQLite, PRAGMA, create_schema
│   ├── repository.py         # UserProfile + UserRepository
│   ├── schedule_models.py    # ORM: семестры, занятия, справочники
│   └── schedule_repository.py# чтение/запись семестра и справочников
├── services/
│   ├── catalog_service.py    # справочники из SQLite
│   ├── schedule_service.py   # расписание дня/недели из SQLite
│   ├── sync_service.py       # синхронизация ЭИОС → SQLite (CLI)
│   └── rate_limiter.py       # антифлуд в памяти процесса
├── tools/
│   └── sync_schedule.py      # CLI синхронизации
└── bot/
    ├── payloads.py           # строгий формат payload кнопок
    ├── validation.py         # валидация ввода
    ├── ui.py                 # клавиатуры и пагинация
    ├── drafts.py             # черновики (SQLite) + TTL
    ├── core.py               # логика диалога (независима от vkbottle)
    ├── keyboards.py          # адаптер к vkbottle
    └── bot.py                # сборка Bot и обработчик
data/
└── bot.db                    # вся локальная база (создаётся автоматически)
tests/                        # pytest
Dockerfile, docker-compose.yml, pyproject.toml, .env.example
```

## Требования

- Python 3.11+ (в Docker используется `python:3.11-slim`)
- Токен сообщества ВК (Long Poll, права «сообщения сообщества» и
  «управление сообществом»)
- Для синхронизации — доступ к `https://eios.kosgos.ru` (напрямую или через
  прокси из `EIOS_PROXY_URL`)

## Быстрый старт

### 1. Собрать базу с расписанием (там, где доступен ЭИОС)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                 # Windows: copy .env.example .env
# при необходимости задайте EIOS_PROXY_URL
python -m src.tools.sync_schedule --year 2026-2027 --all-groups
python -m src.tools.sync_schedule --skip-catalog --teachers-from-groups
```

Появится файл `data/bot.db` со справочниками и расписанием семестра для всех
групп; расписание преподавателей затем строится локально из этих же занятий.
Повторный запуск безопасен: данные цели заменяются целиком, дублей не будет.

### 2. Запустить бота

```bash
python -m src.main
```

Бот создаст/откроет `data/bot.db` (таблицы `users`, `drafts`, `lessons` и др.),
будет читать расписание только из него и общаться с VK через Long Poll.

### Docker

```bash
cp .env.example .env                 # Windows: copy .env.example .env
# укажите VK_BOT_TOKEN
docker compose up -d --build
docker compose logs -f bot
```

Если сервер **не имеет доступа к ЭИОС**:

```bash
# на машине с доступом к ЭИОС
python -m src.tools.sync_schedule --year 2026-2027 --all-groups
# расписание преподавателей строим из занятий групп (без запросов к ЭИОС)
python -m src.tools.sync_schedule --skip-catalog --teachers-from-groups

# перенести готовую базу на сервер в volume bot_data
scp data/bot.db deploy@SERVER:~/app/data/bot.db
cd ~/app
docker compose cp data/bot.db bot:/app/data/bot.db
docker compose exec -u root bot chown 10001:10001 /app/data/bot.db
docker compose restart bot
```

При **последующих обновлениях** сначала забирайте боевую базу с профилями,
синхронизируйте её локально и возвращайте обратно:

```bash
# 1. забрать текущую базу с сервера (профили + старое расписание)
docker compose cp bot:/app/data/bot.db ./bot.db

# 2. на машине с доступом к ЭИОС обновить расписание в этом же файле
python -m src.tools.sync_schedule --db ./bot.db --all-groups --force
python -m src.tools.sync_schedule --db ./bot.db --skip-catalog --teachers-from-groups

# 3. вернуть файл на сервер
docker compose cp ./bot.db bot:/app/data/bot.db
docker compose exec -u root bot chown 10001:10001 /app/data/bot.db
docker compose restart bot
```

Так профили пользователей, созданные ботом, не теряются.

Если серверу доступен ЭИОС (напрямую или через `EIOS_PROXY_URL`), базу можно
обновлять прямо в контейнере:

```bash
docker compose exec bot python -m src.tools.sync_schedule --all-groups
docker compose exec bot python -m src.tools.sync_schedule --skip-catalog --teachers-from-groups
```

## Синхронизация семестра

Единственное место, где выполняются запросы к ЭИОС. Один запрос без `sdate`
отдаёт весь семестр цели.

```bash
# все группы выбранного года (справочники скачиваются автоматически)
python -m src.tools.sync_schedule --year 2026-2027 --all-groups
# группы и преподаватели — рекомендуется для полностью офлайн-сервера
python -m src.tools.sync_schedule --all-groups
python -m src.tools.sync_schedule --skip-catalog --teachers-from-groups
# прямой запрос расписания преподавателей к ЭИОС (медленнее, rate limit)
python -m src.tools.sync_schedule --all-teachers
# только цели уже зарегистрированных пользователей (профили из data/bot.db)
python -m src.tools.sync_schedule --registered-only
# только преподаватели, у которых есть пары в загруженных группах
python -m src.tools.sync_schedule --teachers-with-lessons
# построить расписание преподавателей из занятий групп (без ЭИОС)
python -m src.tools.sync_schedule --skip-catalog --teachers-from-groups
# точечно
python -m src.tools.sync_schedule --group-id 8948 --teacher-id 14
# диагностика
python -m src.tools.sync_schedule --stats
python -m src.tools.sync_schedule --list-targets
```

* `SCHEDULE_SYNC_MIN_INTERVAL` (по умолчанию 3600 с) пропускает недавно
  синхронизированные цели; `--force` отключает проверку.
* ЭИОС ограничивает частоту запросов (около 30/мин). Клиент автоматически
  повторяет `429` с учётом `Retry-After`. Для первой массовой загрузки
  используйте последовательный режим:
  ```bash
  # Linux/macOS
  SCHEDULE_SYNC_REQUEST_DELAY=3.0 python -m src.tools.sync_schedule \
    --all-groups --teachers-with-lessons --concurrency 1
  # Windows (cmd)
  set SCHEDULE_SYNC_REQUEST_DELAY=3.0 && python -m src.tools.sync_schedule ^
    --all-groups --teachers-with-lessons --concurrency 1
  ```
* Ошибка по одной группе не прерывает прогон: она пишется в `sync_state`,
  CLI возвращает код `1` и печатает список ошибок.
* Пустые расписания помечаются `status=empty` и не перезапрашиваются повторно
  (пока не истечёт `SCHEDULE_SYNC_MIN_INTERVAL` или не указан `--force`).
* `--registered-only` читает профили из той же `data/bot.db`. Для сервера без
  доступа к ЭИОС используйте `--all-groups`, чтобы покрыть любые будущие
  профили, и `--teachers-from-groups` для преподавателей.
* При переносе базы замените файл целиком (или снимите копию через
  `sqlite3 .backup`), чтобы не потерять профили, созданные ботом.

## Перенос профилей из старой PostgreSQL (опционально)

Если раньше профили хранились в PostgreSQL, их можно один раз перенести:

```bash
pip install asyncpg
python -m src.tools.migrate_from_postgres \
  --source postgresql://postgres:postgres@localhost:5432/vk_schedule \
  --target data/bot.db
# или задайте DATABASE_URL и не указывайте --source
```

После переноса PostgreSQL больше не нужен. Черновики регистрации не
переносятся — они временные.

## Что лежит в `data/bot.db`


| Таблица | Содержимое |
|---|---|
| `users` | профили ВК (роль, группа/преподаватель) |
| `drafts` | незавершённые регистрации (FSM) с TTL |
| `academic_years`, `groups`, `teachers` | справочники ЭИОС |
| `semesters` | семестры учебного года и их границы |
| `lessons` | занятия семестра (дата, время, аудитория, преподаватель и т.д.) |
| `targets`, `sync_state` | имена целей и состояние их синхронизации |
| `catalog_state` | время обновления справочников |

## Переменные окружения

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `VK_BOT_TOKEN` | Токен сообщества ВК (обязателен) | — |
| `SQLITE_DB_PATH` | Файл локальной базы (профили + расписание) | `data/bot.db` |
| `SCHEDULE_SYNC_CONCURRENCY` | Одновременных запросов к ЭИОС (CLI) | `5` |
| `SCHEDULE_SYNC_MIN_INTERVAL` | Мин. интервал пересинхронизации, сек | `3600` |
| `SCHEDULE_SYNC_REQUEST_DELAY` | Пауза между запросами, сек | `0.2` |
| `EIOS_BASE_URL` | База API ЭИОС (CLI) | `https://eios.kosgos.ru` |
| `EIOS_PROXY_URL` | Прокси для ЭИОС (CLI) | — |
| `EIOS_BROWSER_HEADERS` | Браузерные заголовки (Chrome UA/Referer/Origin) | `false` |
| `EIOS_USER_AGENT` | Свой User-Agent для запросов к ЭИОС | — |
| `TIMEZONE` | Часовой пояс | `Europe/Moscow` |
| `LOG_LEVEL` | Уровень логирования | `INFO` |
| `RATE_LIMIT_MESSAGES` | Лимит сообщений на окно | `20` |
| `RATE_LIMIT_WINDOW` | Окно rate limit, сек | `10` |
| `MAX_TEXT_LENGTH` | Макс. длина ввода | `512` |
| `ONBOARDING_TTL` | TTL черновика регистрации, сек | `900` |
| `DB_ECHO` | Логировать SQL (отладка) | `false` |
| `ENVIRONMENT` | Окружение для Sentry | `production` |
| `SENTRY_DSN` | DSN Sentry (пусто — выключено) | — |
| `HEARTBEAT_URL` | URL пинга мониторинга | — |
| `HEARTBEAT_INTERVAL` | Период пинга, сек | `300` |

## Безопасность

- **SQL-инъекции**: только ORM SQLAlchemy с параметризацией; поля профиля
  ограничены по типам/длинам через Pydantic.
- **Anti-DDoS/DoS**: rate limit по пользователю (в памяти процесса),
  ограничение длины ввода, белый список символов для фамилии, пагинация.
- **Подмена payload**: `extra="forbid"`, белый список действий, лимит 255 байт,
  проверка диапазонов.
- **Ошибки**: пользователю — общее сообщение, детали — в лог/Sentry.
- **IDOR**: расписание строится по сохранённому профилю `vk_id`, а не по данным
  кнопки.
- **Секреты**: токен и прокси только в `.env` (в `.gitignore`).
- **Файл БД**: содержит персональные данные пользователей (VK ID, группа) —
  не публикуйте его и делайте бэкапы.

## Тесты

```bash
pytest -q        # 112 тестов: API, SQLite, миграция, синхронизация, каталог, расписание, UI, ядро
```

## Мониторинг и логирование ошибок

- **Логи контейнера**: `docker compose logs -f bot`; в `docker-compose.yml`
  включена ротация (`json-file`, 10 МБ × 3 файла).
- **Sentry (ошибки)**: создайте проект Python на sentry.io, задайте `SENTRY_DSN`
  в `.env`. Персональные данные не отправляются (`send_default_pii=False`).
- **Heartbeat (бот упал/завис)**: у long-poll бота нет HTTP-эндпоинта,
  используйте «мёртвую руку» (healthchecks.io / Uptime Kuma Push). Бот пингует
  `HEARTBEAT_URL` каждые `HEARTBEAT_INTERVAL` секунд.
- **Аптайм сервера**: Uptime Kuma / Better Stack для проверки VPS.

Подробная инструкция — в [docs/DEPLOY.md](docs/DEPLOY.md).

## Деплой на VPS (кратко)

1. Ubuntu 22.04+, пользователь без root, вход по SSH-ключам, `ufw` (22/80/443).
2. Установить Docker Engine и Compose plugin.
3. Клонировать репозиторий, создать `.env` (`chmod 600`) с `VK_BOT_TOKEN`.
4. Подготовить `data/bot.db`:
   - на машине с доступом к ЭИОС: `python -m src.tools.sync_schedule --all-groups`,
     затем `python -m src.tools.sync_schedule --skip-catalog --teachers-from-groups`,
     после чего скопировать файл в volume `bot_data`;
   - либо на сервере, если ЭИОС доступен (или настроен `EIOS_PROXY_URL`):
     `docker compose exec bot python -m src.tools.sync_schedule --all-groups`, затем
     `docker compose exec bot python -m src.tools.sync_schedule --skip-catalog --teachers-from-groups`.
5. `docker compose up -d --build`, проверить `docker compose ps` и логи.
6. Обновление: `git pull && docker compose up -d --build`.
7. Бэкап: копия `data/bot.db` (или тома `bot_data`) — в ней и профили, и
   расписание.

## Возможные проблемы

- **`no such table: users`**: таблицы создаются при старте бота
  (`SqliteDatabase.create_schema`); запустите `python -m src.main`.
- **Бот молчит**: проверьте `VK_BOT_TOKEN`, включённые «Сообщения сообщества»,
  Long Poll API и права токена.
- **`vkbottle` не установлен**: `pip install vkbottle` или запуск через Docker.
- **Нет расписания**: в локальной БД нет данных. Соберите базу командой
  `python -m src.tools.sync_schedule --all-groups` (и затем
  `--skip-catalog --teachers-from-groups`) на машине с доступом к ЭИОС и
  перенесите `data/bot.db`; либо запустите синхронизацию в контейнере с прокси.
- **`database is locked`**: одновременно работают несколько писателей — не
  запускайте синхронизацию параллельно с копированием базы. WAL и
  `busy_timeout` уже включены.
- **Ошибка 451 при синхронизации**: IP заблокирован сайтом. Запускайте CLI на
  машине с российским IP или задайте `EIOS_PROXY_URL`. Бот при этом продолжает
  работать офлайн на уже сохранённых данных.
- **`PermissionError: '/app/.vkbottle'`**: контейнер собран старым образом —
  `docker compose build --no-cache bot && docker compose up -d --force-recreate bot`.
- **В трейсбеке `asyncpg` / `Connect call failed ... 5432`**: на сервере
  запущен старый образ (до перехода на SQLite). Обновите код и пересоберите:
  `git fetch origin main && git reset --hard origin/main && docker compose up -d --build --remove-orphans`.
  В актуальной версии файла `src/db/base.py` нет, используется `src/db/sqlite_db.py`.
- **Не хватает прав на `bot.db` после копирования**: выполните
  `docker compose exec -u root bot chown 10001:10001 /app/data/bot.db`.
- **Кажется, что «пропал» `data/bot.db` после деплоя**: база лежит не в
  `~/app/data`, а в Docker-volume `bot_data` (смонтирован в `/app/data`).
  `.dockerignore` намеренно не кладёт `data/*.db` в образ, а volume при первом
  запуске SQLite-версии создаётся пустым. Проверка:
  `docker compose exec bot ls -lh /app/data`; загрузка базы:
  `docker compose cp data/bot.db bot:/app/data/bot.db` и
  `docker compose exec -u root bot chown 10001:10001 /app/data/bot.db`
  (подробно — раздел 14 в `docs/DEPLOY.md`).


