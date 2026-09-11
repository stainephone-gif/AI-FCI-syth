# Запуск на своём компьютере

Два режима. **Консольный** не требует Telegram: нужен только ключ API, весь
конвейер печатает результат в терминал. **Полный** поднимает бота как на
сервере, с чатом редакции и каналом-дублёром.

## 1. Установить Python

- **macOS.** Открыть Terminal (Cmd+Пробел, «Terminal»). Проверить: `python3 --version`.
  Если версии нет или она ниже 3.11: `brew install python@3.12`
  (Homebrew ставится с сайта brew.sh).
- **Windows.** Поставить Python 3.12 с python.org, при установке отметить
  «Add python.exe to PATH». Открыть PowerShell. Проверить: `py --version`.

## 2. Скачать проект и зависимости

macOS:
```bash
git clone -b claude/fervent-maxwell-uqv3j7 https://github.com/stainephone-gif/AI-FCI-syth.git
cd AI-FCI-syth
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env
```

Windows (PowerShell):
```powershell
git clone -b claude/fervent-maxwell-uqv3j7 https://github.com/stainephone-gif/AI-FCI-syth.git
cd AI-FCI-syth
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
playwright install chromium
copy .env.example .env
```

Если PowerShell отказывается запускать `Activate.ps1`, один раз выполнить
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` и открыть окно заново.
Если нет git, поставить с git-scm.com или скачать zip проекта с GitHub
(кнопка Code → Download ZIP, ветка `claude/fervent-maxwell-uqv3j7`).

Команда `playwright install chromium` скачивает браузер для карточек,
около 150 МБ. Без него всё работает, кроме карточек.

## 3. Заполнить `.env`

Открыть файл `.env` в любом текстовом редакторе и выбрать провайдера моделей:

```
MODEL_PROVIDER=gigachat            # или anthropic, или openai
GIGACHAT_CREDENTIALS=...           # для gigachat
ANTHROPIC_API_KEY=...              # для anthropic
OPENAI_BASE_URL=... OPENAI_API_KEY=... RANK_MODEL=... DRAFT_MODEL=...   # для openai: Qwen, Kimi, OpenRouter
```

Проверка ключа: `python -m app.cli models` печатает доступные модели.

`BOT_TOKEN` для консольного режима можно оставить как в примере. База
создастся сама в `data/syth.db`.

## 4. Консольный режим

Всё запускается из папки проекта при активированном окружении
(в начале строки терминала видно `(.venv)`).

```bash
python -m app.cli collect          # сбор источников и ранжирование, топ-10 в терминал
python -m app.cli digest           # черновики для лучших кандидатов, текст в терминал
python -m app.cli draft 12         # черновик для конкретного поста
python -m app.cli post "в четверг семинар с ИТМО, ссылка https://..."
python -m app.cli card "Заголовок карточки" "Подзаголовок"   # data/cards/test.png
python -m app.cli status
```

Порядок для первой проверки: `collect`, потом `digest`. Текст черновиков
печатается без разметки, с блоками предупреждений, как их увидит редактор.
Карточки лежат в `data/cards/`.

Что смотреть: какие источники отвечают (ошибки в отчёте `collect`),
насколько оценки совпадают с вашим чутьём, попадает ли черновик в стиль,
что модель не смогла подтвердить.

Промпты правятся прямо в `prompts/`, повторный `digest` подхватит их сразу.
Чтобы переписать черновик после правки промпта, нужен новый кандидат:
`digest` берёт только посты в статусе ranked. Проще всего удалить
`data/syth.db` и пройти `collect` заново.

## 5. Полный режим с Telegram

Нужны бот, чат редакции и канал-дублёр из раздела 1 инструкции
`docs/TEST-WEEK.md`. В `.env` заполнить `BOT_TOKEN`, `EDITOR_IDS`,
`EDITOR_CHAT_ID`, `CHANNEL_ID`. Затем:

```bash
python -m app.main
```

Бот работает, пока открыт терминал. Ctrl+C останавливает. Всё, что описано
для сервера в `docs/TEST-WEEK.md`, работает так же, только вместо
`docker compose logs` лог виден прямо в терминале.

## 6. Тесты

```bash
pytest -q
```

Не ходят в сеть и не тратят токены. Если они проходят, окружение собрано верно.

## Частые проблемы

- **`ModuleNotFoundError: app`**: терминал открыт не в папке проекта или
  окружение не активировано.
- **Ошибка сети при `collect`**: с компьютера недоступны сайты источников
  или API моделей. Проверить в браузере, при необходимости VPN.
- **`playwright` жалуется на браузер**: повторить `playwright install chromium`
  или указать путь к своему Chrome в `CHROMIUM_PATH`.
- **Бот молчит в группе**: выключена ли приватность в @BotFather и есть ли
  ваш id в `EDITOR_IDS`. Команда `/whoami` работает всегда.
