FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        git \
        libgomp1 \
        nodejs \
        npm \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir uv \
    && npm install --global pnpm@9.15.9

WORKDIR /workspace

# Dependency layers depend only on lock files. Application startup never
# resolves or downloads project dependencies.
COPY backend/pyproject.toml backend/uv.lock /workspace/backend/
RUN uv sync --project /workspace/backend --locked

COPY frontend/package.json frontend/pnpm-lock.yaml /workspace/frontend/
RUN pnpm --dir /workspace/frontend install --frozen-lockfile

# Copy an initial application snapshot. Compose overlays only source folders,
# leaving the Linux dependency trees owned by the image.
COPY backend/app /workspace/backend/app
COPY backend/alembic /workspace/backend/alembic
COPY backend/alembic.ini /workspace/backend/alembic.ini
COPY frontend /workspace/frontend
RUN pnpm --dir /workspace/frontend run build

COPY docker/entrypoint.sh /usr/local/bin/neural-deck-dev
RUN chmod 0755 /usr/local/bin/neural-deck-dev \
    && mkdir -p /var/lib/neural-deck /var/log/neural-deck

EXPOSE 3000 8080
ENTRYPOINT ["/usr/local/bin/neural-deck-dev"]
