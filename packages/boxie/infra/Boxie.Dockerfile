FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.15-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY packages/boxie/pyproject.toml packages/boxie/pyproject.toml
COPY packages/boxie/src packages/boxie/src
COPY packages/boxie/alembic packages/boxie/alembic
# CI validates the workspace lock; frozen sync needs only this package's sources.
RUN uv sync --frozen --package boxie --no-dev --no-editable --no-python-downloads
FROM python:3.15-slim
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
CMD ["boxie-api", "--config", "/etc/boxie/api.json"]
