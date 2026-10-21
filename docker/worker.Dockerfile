FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY packages/core /app/packages/core
RUN pip install --no-cache-dir /app/packages/core

COPY services/worker/app /app/app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home app \
    && mkdir -p /app/data/files \
    && chown -R app:app /app

USER app

CMD ["celery", "-A", "app.celery_app:celery_app", "worker", "--loglevel=INFO"]
