FROM ghcr.io/voidzero-dev/vite-plus:1.2.0 AS frontend
WORKDIR /frontend
COPY --chown=vp:vp tools/cli-playground/frontend/package.json tools/cli-playground/frontend/pnpm-lock.yaml tools/cli-playground/frontend/pnpm-workspace.yaml tools/cli-playground/frontend/.node-version ./
RUN vp install --frozen-lockfile
COPY --chown=vp:vp tools/cli-playground/frontend/ ./
RUN vp build --outDir /frontend/dist

FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.15-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY tools/boxie/pyproject.toml tools/boxie/pyproject.toml
COPY tools/boxie/infra/observability/pyproject.toml tools/boxie/infra/observability/pyproject.toml
COPY tools/boxie/infra/ingress/pyproject.toml tools/boxie/infra/ingress/pyproject.toml
COPY tools/boxie/tests/support/pyproject.toml tools/boxie/tests/support/pyproject.toml
COPY tools/boxie/infra/pyproject.toml tools/boxie/infra/pyproject.toml
COPY tools/cli-playground/pyproject.toml tools/cli-playground/pyproject.toml
COPY tools/cli-playground/infra/pyproject.toml tools/cli-playground/infra/pyproject.toml
COPY tools/cli-bench/pyproject.toml tools/cli-bench/pyproject.toml
COPY tools/cli-testing/pyproject.toml tools/cli-testing/pyproject.toml
COPY tools/cli-playground/src/playground tools/cli-playground/src/playground
COPY --from=frontend /frontend/dist/ tools/cli-playground/src/playground/static/
RUN uv sync --locked --package cli-playground --no-dev --no-editable --no-python-downloads
FROM python:3.15-slim
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "playground.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "65536", "--no-proxy-headers"]
