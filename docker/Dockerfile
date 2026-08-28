FROM node:22.13.1-bookworm-slim AS node-runtime

FROM python:3.12-slim-bookworm

COPY --from=node-runtime /usr/local /usr/local

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/workspace/backend/.venv

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates \
    && npm install --global pnpm@11.4.0 \
    && pip install --no-cache-dir uv==0.10.0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN uv sync --project backend --extra dev --frozen

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./frontend/
RUN pnpm --dir frontend install --frozen-lockfile

COPY backend ./backend
COPY frontend ./frontend
COPY scripts ./scripts

RUN chmod +x scripts/*.sh

EXPOSE 3000
CMD ["bash", "/workspace/scripts/start_local.sh"]
