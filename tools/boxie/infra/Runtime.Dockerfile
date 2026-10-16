FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.15-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY tools/boxie/pyproject.toml tools/boxie/pyproject.toml
COPY tools/boxie/tests/support/pyproject.toml tools/boxie/tests/support/pyproject.toml
COPY tools/boxie/infra/pyproject.toml tools/boxie/infra/pyproject.toml
COPY tools/cli-playground/pyproject.toml tools/cli-playground/pyproject.toml
COPY tools/cli-playground/infra/pyproject.toml tools/cli-playground/infra/pyproject.toml
COPY tools/cli-bench/pyproject.toml tools/cli-bench/pyproject.toml
COPY tools/cli-testing/pyproject.toml tools/cli-testing/pyproject.toml
COPY tools/boxie/src tools/boxie/src
COPY tools/boxie/alembic tools/boxie/alembic
RUN uv sync --locked --package boxie --no-dev --no-editable --no-python-downloads
FROM python:3.15-slim
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
CMD ["boxie-api", "--config", "/etc/boxie/api.json"]
