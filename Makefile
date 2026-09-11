# Локальная разработка и деплой. Сервер задаётся переменной SERVER=user@host.
SERVER ?=
REMOTE_DIR ?= /opt/ai-fci-syth

.PHONY: venv run test lint deploy logs

venv:
	python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -e ".[dev]"
	.venv/bin/playwright install chromium

run:
	.venv/bin/python -m app.main

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check app tests

# Первый раз на сервере: git clone в $(REMOTE_DIR), cp .env.example .env, заполнить.
deploy:
	@test -n "$(SERVER)" || (echo "Укажите SERVER=user@host"; exit 1)
	ssh $(SERVER) 'cd $(REMOTE_DIR) && git pull --ff-only && docker compose up -d --build'

logs:
	@test -n "$(SERVER)" || (echo "Укажите SERVER=user@host"; exit 1)
	ssh $(SERVER) 'cd $(REMOTE_DIR) && docker compose logs -f --tail=200 bot'
