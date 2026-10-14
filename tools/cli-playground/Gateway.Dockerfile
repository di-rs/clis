FROM ghcr.io/voidzero-dev/vite-plus:1.2.0 AS frontend
WORKDIR /app
COPY --chown=vp:vp tools/cli-playground/frontend/package.json tools/cli-playground/frontend/pnpm-lock.yaml tools/cli-playground/frontend/pnpm-workspace.yaml tools/cli-playground/frontend/.node-version ./
RUN vp install --frozen-lockfile
COPY --chown=vp:vp tools/cli-playground/frontend/ ./
RUN vp build

FROM ghcr.io/astral-sh/uv:0.13.0 AS uv
FROM python:3.15-slim
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY tools/cli-bench/pyproject.toml tools/cli-bench/pyproject.toml
COPY tools/cli-playground/pyproject.toml tools/cli-playground/pyproject.toml
COPY tools/cli-testing/pyproject.toml tools/cli-testing/pyproject.toml
RUN uv sync --locked --all-packages --no-dev --no-python-downloads
COPY tools/cli-playground/playground/ tools/cli-playground/playground/
COPY --from=frontend /app/dist/ tools/cli-playground/frontend/dist/
ENV PYTHONPATH=/app/tools/cli-playground
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "playground.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "65536", "--no-proxy-headers"]
