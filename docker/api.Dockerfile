FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

ARG INSTALL_DEV=false

# Third-party dependencies are keyed only on the manifests, so editing source
# below never re-downloads pip packages.
COPY services/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/api/requirements-dev.txt /app/requirements-dev.txt
RUN if [ "$INSTALL_DEV" = "true" ]; then \
        pip install --no-cache-dir -r /app/requirements-dev.txt; \
    fi

COPY packages/core/pyproject.toml /tmp/core/pyproject.toml
RUN python -c "import tomllib; print(' '.join(tomllib.load(open('/tmp/core/pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/core-deps.txt \
    && pip install --no-cache-dir -r /tmp/core-deps.txt

COPY packages/core /app/packages/core
RUN pip install --no-cache-dir --no-deps /app/packages/core

COPY services/api/app /app/app
COPY alembic.ini /app/alembic.ini
COPY migrations /app/migrations

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --create-home app \
    && mkdir -p /app/data/files \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
