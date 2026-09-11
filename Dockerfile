FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv/app

COPY pyproject.toml ./
COPY app ./app
RUN pip install .

COPY prompts ./prompts
COPY sources.yaml ./

RUN useradd -r -u 10001 syth && mkdir -p /srv/app/data && chown -R syth /srv/app
USER syth

CMD ["python", "-m", "app.main"]
