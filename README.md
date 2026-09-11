# AI-FCI-syth

Агент синтетической редакции для Telegram-канала лаборатории ФКИ:
мониторинг источников, черновики постов, карточки, очередь публикаций
с обязательным одобрением редактора.

Архитектура и план запуска: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Локальный запуск

```bash
make venv                  # виртуальное окружение и зависимости
cp .env.example .env       # заполнить BOT_TOKEN и EDITOR_IDS
make run                   # бот и планировщик в одном процессе
make test                  # тесты
```

По умолчанию база SQLite в `./data/syth.db`, ничего ставить не нужно.

## Деплой на сервер

Один раз на сервере:

```bash
git clone <repo> /opt/ai-fci-syth && cd /opt/ai-fci-syth
cp .env.example .env       # заполнить
docker compose up -d --build
```

Дальше с рабочей машины:

```bash
make deploy SERVER=user@host   # git pull и пересборка
make logs SERVER=user@host
```

В `docker-compose.yml` бот работает с Postgres; переменная `DATABASE_URL` из
`.env` при этом переопределяется.

## Что уже работает

Шаг 10 плана: конфиг, схема базы, allowlist редакторов, команды
`/status`, `/queue`, `/help`, планировщик с задачами-заглушками, деплой.
Сбор источников, черновики и публикация появятся на следующих шагах.
