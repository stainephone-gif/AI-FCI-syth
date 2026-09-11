# Образ Playwright с Chromium и шрифтами; версия совпадает с pyproject.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv/app

COPY pyproject.toml ./
COPY app ./app
RUN pip install .

COPY prompts ./prompts
COPY sources.yaml ./

RUN useradd -r -u 10001 syth && mkdir -p /srv/app/data && chown -R syth /srv/app
USER syth
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

CMD ["python", "-m", "app.main"]
