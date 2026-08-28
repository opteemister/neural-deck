# LLM Studio

LLM Studio is a self-hosted Linux web service for launching and managing local LLM models through multiple execution engines. It provides a browser dashboard backed by an async FastAPI service and a React/Vite frontend.

## Development

The local development runtime uses one Docker Compose container. Vite rebuilds the frontend in watch mode, while FastAPI serves the generated SPA and API from one port. SQLite data is persisted in the repository's ignored `data/` directory.

```bash
docker compose -f docker/docker-compose.yml up --build
```

Open <http://127.0.0.1:3000> in a browser. The API health check is available at <http://127.0.0.1:3000/api/health/live>.

## Production

Production runs as a native systemd service on Linux. Build an archive and install it on the target host:

```bash
./scripts/make_deploy_archive.sh
sudo install -d -m 0750 /etc/llm-studio
sudo install -m 0640 deploy/llm-studio.env.example /etc/llm-studio/llm-studio.env
sudoedit /etc/llm-studio/llm-studio.env
sudo ./scripts/install_systemd_service.sh
```

The archive excludes local state and all `.env` files. The service stores SQLite data and engine files in `/var/lib/llm-studio`, and model logs in `/var/log/llm-studio`. Copy the production environment file to `/etc/llm-studio/llm-studio.env` before installation.

## Quality

```bash
./verify/run-verify.sh
```
