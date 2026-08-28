# AGENTS.md

The project's entry document for all agents. Hierarchy: **constitution → tech defaults → this file → specs/tasks**. The constitution and defaults are global (`~/.claude/loop-engineering/constitution.md` or `~/.agents/loop-engineering/constitution.md`, and the matching `tech-defaults.md`); on conflict the higher document wins and the conflict is escalated.

## Project

LLM Studio is a self-hosted Linux web service for launching and managing local large language models through multiple execution engines. It provides a browser-based dashboard for configuring engines, starting and stopping model processes, and observing their status and logs.

The project is intended for an operator running the service on a remote Linux machine with local model files and hardware. The first iteration establishes the service shell and deployment model; engine-specific behavior will be defined in later specifications.

## Stack

- Backend: Python 3.12, FastAPI, uvicorn, SQLAlchemy 2.x async with aiosqlite, Alembic, Pydantic Settings, httpx
- Frontend: React + Vite + TypeScript strict + Tailwind CSS + shadcn/ui + React Router + TanStack Query
- Frontend quality: ESLint flat config, Vitest, React Testing Library
- Persistence: SQLite in a dedicated data directory
- Package managers: uv / pnpm
- Local runtime: one Docker Compose application container containing the backend and frontend development processes
- Production runtime: native Linux systemd service serving the built SPA from the backend

### Deviations from tech-defaults

| Deviation | Reason |
|---|---|
| SQLite instead of Supabase/Postgres | The service is self-hosted on one Linux machine and needs a small local persistent store without an external database service. |
| SQLAlchemy async instead of the sync default | Model process control, long-running operations, and streaming status/log data are expected to require non-blocking I/O. |
| One local container for frontend and backend | Local development should reproduce the single application boundary used by the operator. |
| Native systemd production deployment instead of a server Docker Compose overlay | The production target is a Linux service managed by the host, with model files and persistent data owned by host directories. |
| No production migration container | Alembic runs as an explicit installation/release step before the systemd service starts. |

## Commands

Agents use only these commands in the sandbox, without permission requests:

```bash
# backend tests
cd backend && uv run --extra dev pytest

# backend lint and types
cd backend && uv run --extra dev ruff check .
cd backend && uv run --extra dev ruff format --check .
cd backend && uv run --extra dev mypy .

# frontend checks
cd frontend && pnpm test
cd frontend && pnpm run lint
cd frontend && pnpm run typecheck
cd frontend && pnpm run build

# local development
docker compose -f docker/docker-compose.yml up --build

# production archive and installation
./scripts/make_deploy_archive.sh
sudo ./scripts/install_systemd_service.sh
```

The local application is available at `http://127.0.0.1:5173`; the backend API is at `http://127.0.0.1:8000`.

## Verification rules

- Full verification: `./verify/run-verify.sh` — run only by verifier/spec-checker, never by the developer (constitution II.9).
- `verify/` is edited only between tasks; the canonical version from the base branch always runs.
- Tests mock all external resources; real databases and external services are forbidden in tests (constitution II.11).
- Every test file carries a `# spec: {spec name}` marker.
- The native systemd installation path cannot be live-tested on the macOS development host; static checks and the sandbox verification cover the repository-side contract only.
- SQLite data is runtime state, not source: local data belongs in `data/`, and production data belongs in `/var/lib/llm-studio`.

## Git

- Base branch: `main` — merged only by a human via PR.
- Spec branch: `feature/{YYYY-MM-DD}-{spec-name}`.
- Task branch (= worktree): `task/{spec-name}/{subtask-slug}`; deleted after merging into the feature branch.
- Worktrees: `.worktrees/{task-slug}` (gitignored).

## Process layout

- Specs: `docs/specs/{YYYY-MM-DD}-{slug}.md`; addenda are next to the spec with the same prefix and a short change title suffix.
- Tasks: `tasks/{spec-name}/{YYYY-MM-DD}-{subtask-slug}.md`.
- Results: `docs/spec-results/`.

## Language

Project documentation is written in English for GitHub publication. Code, comments, tests, configuration, and all implementation artifacts are always 100% English. Code identifiers, commands, API paths, and config keys remain in their original form.

## Runtime and deployment

- `.env.example` is the tracked variable contract; `.env.local` is used by local Compose; `.env.proxy` is the ignored production variable file copied to the host's service environment directory.
- Any variable addition or rename updates all three env files together.
- Technical secrets belong in environment files; business or integration secrets belong in the appropriate secret store, never in source control.
- Local Compose bind-mounts the source tree and `./data` into one application container. The backend runs on port 8000 and Vite runs on port 5173 in that container.
- Production builds `frontend/dist`, runs Alembic before starting systemd, and serves the SPA through FastAPI. The production service owns `/var/lib/llm-studio` for SQLite and `/var/log/llm-studio` for model logs.

## Detail-resolution decisions

The initial configuration intentionally contains only health and application-shell behavior. Model engine interfaces, process supervision, authentication, and dashboard workflows are deferred to specifications. Implementation details not covered by a specification are resolved by the developer according to this document and the technical defaults, then recorded in the task handoff.
