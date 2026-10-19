FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.15-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY infra/host/pyproject.toml infra/host/pyproject.toml
COPY packages/boxie/pyproject.toml packages/boxie/pyproject.toml
COPY packages/boxie/tests/support/pyproject.toml packages/boxie/tests/support/pyproject.toml
COPY packages/boxie/infra/pyproject.toml packages/boxie/infra/pyproject.toml
COPY tools/cli-playground/pyproject.toml tools/cli-playground/pyproject.toml
COPY tools/cli-playground/infra/pyproject.toml tools/cli-playground/infra/pyproject.toml
COPY tools/cli-bench/pyproject.toml tools/cli-bench/pyproject.toml
COPY tools/cli-check/pyproject.toml tools/cli-check/pyproject.toml
COPY packages/boxie/src packages/boxie/src
COPY packages/boxie/alembic packages/boxie/alembic
RUN uv sync --locked --package boxie --no-dev --no-editable --no-python-downloads
FROM python:3.15-slim
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
CMD ["boxie-api", "--config", "/etc/boxie/api.json"]
