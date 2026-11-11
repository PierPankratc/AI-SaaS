# VK-бот расписания КГУ (ЭИОС)

Бот ВКонтакте для просмотра расписания Костромского государственного университета.
Источник данных — открытый JSON API ЭИОС КГУ (`https://eios.kosgos.ru`).
Справочники и расписание кэшируются в Redis, профиль пользователя хранится в PostgreSQL.

## Возможности

- **Регистрация один раз** (данные сохраняются в БД):
  роль → институт → ступень обучения → курс → группа.
  Ступени: бакалавриат, специалитет, магистратура, аспирантура, СПО.
- **Преподаватель**: вводит фамилию текстом (единственный ввод), затем выбирает
  себя из найденных однофамильцев кнопками.
- **Главное меню кнопками**: «На сегодня», «На завтра», «На эту неделю»,
  «Сменить группу / профиль».
- **Кэш Redis**: справочники (24 ч), расписание (30 мин) + резервная копия на
  7 дней — бот продолжает работать при недоступности сайта вуза.
- **Безопасность**: антифлуд (rate limit), валидация ввода, строгий разбор
  payload кнопок, защита от SQL-инъекций через ORM, безопасные ошибки.

## Архитектура

```
VK (long poll) ──► BotCore ──► CatalogService ──► EiosClient ──► eios.kosgos.ru
                     │              │
                     │              └─► RedisCache (справочники)
                     ├─► ScheduleService ──► RedisCache (расписание + резерв)
                     ├─► UserRepository ──► PostgreSQL
                     └─► RateLimiter ──► Redis
```

## Структура проекта

```
src/
├── config.py                # настройки (.env, pydantic-settings)
├── main.py                  # запуск: БД, Redis, прогрев, long poll
├── monitoring.py            # Sentry + heartbeat
├── api/
│   ├── schemas.py           # модели API и классификация ступеней
│   └── eios_client.py       # httpx-клиент ЭИОС (повторы, таймауты)
├── cache/
│   └── redis_cache.py       # кэш справочников и расписания
├── db/
│   ├── models.py            # ORM-модель User
│   ├── base.py              # движок и фабрика сессий
│   └── repository.py        # UserProfile + UserRepository
├── services/
│   ├── catalog_service.py   # институты/ступени/курсы/группы, поиск ФИО
│   ├── schedule_service.py  # расписание дня/недели и форматирование
│   └── rate_limiter.py      # ограничение частоты запросов
└── bot/
    ├── payloads.py          # строгий формат payload кнопок
    ├── validation.py        # валидация ввода
    ├── ui.py                # спецификации клавиатур и пагинация
    ├── drafts.py            # черновики регистрации (FSM)
    ├── core.py              # логика диалога (независима от vkbottle)
    ├── keyboards.py         # адаптер в vkbottle
    └── bot.py               # сборка Bot и обработчик
tests/                       # pytest
Dockerfile, docker-compose.yml, pyproject.toml, .env.example
```

## Требования

- Python 3.11+ (в Docker используется `python:3.11-slim`)
- PostgreSQL 16 и Redis 7 (поднимаются через `docker compose`)
- Токен сообщества ВК (Long Poll, права «сообщения сообщества» и «управление сообществом»)

## Быстрый старт (Docker)

```bash
# Windows: copy .env.example .env
cp .env.example .env
# откройте .env: укажите VK_BOT_TOKEN и надёжный POSTGRES_PASSWORD
docker compose up -d --build
docker compose logs -f bot
```

## Локальный запуск (без Docker)

```bash
docker compose up -d postgres redis        # только БД и кэш
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                        # указать VK_BOT_TOKEN
python -m src.main
```

## Переменные окружения

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `VK_BOT_TOKEN` | Токен сообщества ВК (обязателен) | — |
| `DATABASE_URL` | DSN PostgreSQL (asyncpg) | `...localhost:5432/vk_schedule` |
| `REDIS_URL` | DSN Redis | `redis://localhost:6379/0` |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Реквизиты для docker-compose | `postgres` / `postgres` / `vk_schedule` |
| `EIOS_BASE_URL` | База API ЭИОС | `https://eios.kosgos.ru` |
| `CATALOG_CACHE_TTL` | TTL справочников, сек | `86400` |
| `SCHEDULE_CACHE_TTL` | TTL расписания, сек | `1800` |
| `SCHEDULE_FALLBACK_TTL` | TTL резерва расписания, сек | `604800` |
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

- **SQL-инъекции**: только ORM SQLAlchemy с параметризацией, без строкового SQL;
  поля профиля ограничены по типам/длинам через Pydantic.
- **Anti-DDoS/DoS**: rate limit по пользователю (Redis/память), ограничение длины
  ввода, белый список символов для фамилии, пагинация списков, таймауты и
  ограниченные ретраи запросов к ЭИОС, кэширование ответов.
- **Подмена payload**: `extra="forbid"`, белый список действий, лимит 255 байт,
  проверка диапазонов.
- **Ошибки**: пользователю — общее сообщение, детали — в лог/Sentry.
- **IDOR**: расписание строится по сохранённому профилю `vk_id`, а не по данным кнопки.
- **Секреты**: токен и пароли только в `.env` (в `.gitignore`).

> ⚠️ Не публикуйте вывод `docker compose config` и не коммитьте `.env` — они
> раскрывают значения переменных. Если токен ВК или пароль БД где-то засветились
> (чат, лог, скриншот) — перевыпустите токен и смените пароль.

## Тесты

```bash
pytest -q        # 80 тестов: API, кэш, каталог, расписание, БД, безопасность, UI, ядро
```

## Мониторинг и логирование ошибок

- **Логи контейнера**: `docker compose logs -f bot`; в `docker-compose.yml`
  включена ротация (`json-file`, 10 МБ × 3 файла), чтобы диск не переполнялся.
- **Sentry (ошибки)**: создайте проект Python на sentry.io, задайте `SENTRY_DSN` в
  `.env`. Исключения уходят автоматически (`src/monitoring.py`), персональные
  данные не отправляются (`send_default_pii=False`).
- **Heartbeat (бот упал/завис)**: у long-poll бота нет HTTP-эндпоинта, поэтому
  используйте «мёртвую руку»: создайте чек в healthchecks.io или Uptime Kuma
  (Push-монитор) и укажите `HEARTBEAT_URL`. Бот пингует URL каждые
  `HEARTBEAT_INTERVAL` секунд; при пропуске пингов сервис пришлёт алерт.
- **Аптайм сервера**: Uptime Kuma / Better Stack для проверки VPS.

Подробная инструкция по VPS, мониторингу и бэкапам — в [docs/DEPLOY.md](docs/DEPLOY.md).

## Деплой на VPS (кратко)

1. Ubuntu 22.04+, пользователь без root, вход по SSH-ключам, `ufw` (22/80/443).
2. Установить Docker Engine и Compose plugin.
3. Клонировать репозиторий, создать `.env` (`chmod 600`) с `VK_BOT_TOKEN` и
   сильным `POSTGRES_PASSWORD`.
4. `docker compose up -d --build`, проверить `docker compose ps` и логи.
5. Обновление: `git pull && docker compose up -d --build`.
6. Бэкап БД: `docker compose exec -T postgres pg_dump -U postgres vk_schedule > backup.sql`.

Полная пошаговая инструкция (подготовка сервера, мониторинг, бэкапы, безопасность) —
в [docs/DEPLOY.md](docs/DEPLOY.md).

## Возможные проблемы

- **Порт занят**: остановите локальные PostgreSQL/Redis или измените порты в compose.
- **`relation "users" does not exist`**: таблицы создаются при старте бота
  (`Database.create_schema`); запустите `python -m src.main`.
- **Бот молчит**: проверьте `VK_BOT_TOKEN`, включённые «Сообщения сообщества»,
  Long Poll API и права токена.
- **`vkbottle` не установлен**: `pip install vkbottle` или запуск через Docker.
- **Нет расписания**: проверьте доступность `https://eios.kosgos.ru` и логи
  (используется резервная копия из кэша при сбоях).



