FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies are keyed only on the manifest, so editing core source below
# never re-resolves or re-downloads the Python dependency tree.
COPY packages/core/pyproject.toml /tmp/core/pyproject.toml
RUN python -c "import tomllib; print(chr(10).join(tomllib.load(open('/tmp/core/pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/core-deps.txt \
    && pip install --no-cache-dir -r /tmp/core-deps.txt

COPY packages/core /app/packages/core
RUN pip install --no-cache-dir --no-deps /app/packages/core

COPY services/worker/app /app/app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home app \
    && mkdir -p /app/data/files \
    && chown -R app:app /app

USER app

CMD ["celery", "-A", "app.celery_app:celery_app", "worker", "--loglevel=INFO"]
