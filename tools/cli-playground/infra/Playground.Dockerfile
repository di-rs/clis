FROM ghcr.io/astral-sh/uv:0.13.1 AS uv
FROM ghcr.io/voidzero-dev/vite-plus:1.2.0 AS frontend
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /workspace
COPY --chown=vp:vp . .
RUN project=$(uv workspace dir --package cli-playground) \
    && cd "$project/frontend" \
    && vp install --frozen-lockfile \
    && vp build --outDir /tmp/frontend-dist

FROM python:3.15-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY . .
COPY --from=frontend /tmp/frontend-dist/ /tmp/frontend-assets/
RUN project=$(uv workspace dir --package cli-playground) \
    && mkdir -p "$project/src/playground/static" \
    && cp -a /tmp/frontend-assets/. "$project/src/playground/static/"
RUN uv sync --locked --package cli-playground --no-dev --no-editable --no-python-downloads
FROM python:3.15-slim
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "playground.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--ws-max-size", "65536", "--no-proxy-headers"]
