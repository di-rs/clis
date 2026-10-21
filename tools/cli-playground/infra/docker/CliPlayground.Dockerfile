FROM ghcr.io/voidzero-dev/vite-plus:1.2.0 AS frontend
WORKDIR /frontend
COPY --chown=vp:vp tools/cli-playground/frontend/package.json tools/cli-playground/frontend/pnpm-lock.yaml tools/cli-playground/frontend/pnpm-workspace.yaml tools/cli-playground/frontend/.node-version ./
RUN vp install
COPY --chown=vp:vp tools/cli-playground/frontend/ ./
RUN vp build --outDir /frontend/dist

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
COPY tools/cli-playground/src/cli_playground tools/cli-playground/src/cli_playground
COPY --from=frontend /frontend/dist/ tools/cli-playground/src/cli_playground/static/
RUN uv sync --package cli-playground --no-dev --no-editable --no-python-downloads
FROM python:3.15-slim
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY tools/cli-playground/alembic.ini /app/alembic.ini
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "cli_playground.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "65536", "--no-proxy-headers"]
