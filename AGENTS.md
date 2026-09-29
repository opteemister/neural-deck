# AGENTS.md

This is the entry document for agents working on LLM Studio. Follow the global
constitution and technical defaults where applicable. Approved specifications
will define the implementation.

## Project

LLM Studio is planned as a self-hosted web service for launching and managing
local language models. Its product behavior and technical design are being
specified from the beginning.

## Current state

- `main` contains only this document and `README.md`.
- The previous application shell is preserved on `codex/initial-shell-backup`
  for reference. It is not an approved implementation baseline.
- No application, deployment workflow, verification scripts, or specifications
  are currently implemented on `main`.

## Agreed constraints for the upcoming specification

- Development happens on the local computer using Docker, without maintaining
  project dependencies directly on the macOS host.
- The intended production target is a Proxmox LXC container reachable by SSH.
  Running Docker inside that LXC is not part of the intended deployment.
- Production builds must not require project dependencies or a build on macOS.
  Keep the first deployment workflow simple; its exact steps remain to be
  specified.

## Process

- Discuss and approve a specification before implementing the new application.
- Write project documentation and implementation artifacts in English.
- Keep secrets and runtime data out of source control.
