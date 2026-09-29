FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NGINX_ENTRYPOINT_QUIET_LOGS=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        nodejs \
        npm \
        nginx \
    && rm -rf /var/lib/apt/lists/*

# uv is used only inside the development container and the native Linux
# service. Installing it in the image keeps project dependencies off macOS.
RUN python -m pip install --no-cache-dir uv

# The frontend is locked with pnpm-lock.yaml. Pin a Node 18 compatible pnpm
# release so the container and native deployment use the same lockfile
# protocol instead of silently resolving a second dependency graph with npm.
RUN npm install --global pnpm@9.15.9

COPY nginx.conf /etc/nginx/nginx.conf
COPY entrypoint.sh /usr/local/bin/llm-studio-dev
RUN chmod 0755 /usr/local/bin/llm-studio-dev \
    && mkdir -p /workspace /var/lib/llm-studio /var/log/llm-studio \
    && chown -R nobody:nogroup /workspace /var/lib/llm-studio /var/log/llm-studio

WORKDIR /workspace
EXPOSE 3000

ENTRYPOINT ["/usr/local/bin/llm-studio-dev"]
