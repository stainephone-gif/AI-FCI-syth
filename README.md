# AI-FCI-syth

Агент синтетической редакции для Telegram-канала лаборатории ФКИ:
мониторинг источников, черновики постов, карточки, очередь публикаций
с обязательным одобрением редактора.

Архитектура и план запуска: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
Тестовая неделя по шагам: [docs/TEST-WEEK.md](docs/TEST-WEEK.md).
Запуск на своём компьютере, в том числе без Telegram: [docs/LOCAL.md](docs/LOCAL.md), отдельно для Windows: [docs/WINDOWS.md](docs/WINDOWS.md).

## Локальный запуск

```bash
make venv                  # виртуальное окружение и зависимости
cp .env.example .env       # заполнить BOT_TOKEN и EDITOR_IDS
make run                   # бот и планировщик в одном процессе
make test                  # тесты
```

Провайдер моделей выбирается в `.env`: `MODEL_PROVIDER=anthropic|gigachat|openai`,
последний покрывает любой OpenAI-совместимый API (Qwen, Kimi, OpenRouter).
Проверка ключа: `python -m app.cli models`.

Отладка без Telegram, нужен только ключ API:

```bash
.venv/bin/python -m app.cli collect   # сбор и ранжирование в терминал
.venv/bin/python -m app.cli digest    # черновики в терминал
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

Шаги 10–13 плана:

- конфиг, схема базы, allowlist редакторов, деплой (шаг 10);
- сбор из RSS, Atom и arXiv, дедупликация, ранжирование моделью, `/collect` (шаг 11);
- черновики по стайлгайду, проверка цитат по источнику, кнопки в редакторском
  чате, второе одобрение для постов с ⚠️, правка ответом на сообщение, `/digest` (шаг 12);
- очередь и публикация в канал в назначенное время, `/queue`, `/cancel N`,
  `/publish N`, защита от повторной отправки, перевооружение очереди после
  перезапуска (шаг 13);
- карточка 1080×1350 к каждому черновику: HTML-шаблон в `app/cards/templates/`,
  рендер через Chromium, цвет плашки по теме, фирменные шрифты кладутся в
  `app/cards/assets/fonts/` (шаг 14);
- ручной ввод: `/post в четверг семинар с ИТМО, ссылка …` превращается в
  черновик-анонс с карточкой «Лаборатория», относительные даты переводятся
  в конкретные и показываются редактору на проверку (шаг 15).

MVP по плану собран. Дальше тестовая неделя на закрытом канале.

Локально для карточек нужен Chromium: `.venv/bin/playwright install chromium`
или путь к уже установленному браузеру в `CHROMIUM_PATH`.
