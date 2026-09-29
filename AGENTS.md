# AGENTS.md

This is the entry document for agents working on LLM Studio. Follow the global
constitution and technical defaults where applicable. Approved specifications
will define the implementation.

Deployment requirements are recorded in `DEPLOYMENT_RULES.md`.

## Project

LLM Studio is planned as a self-hosted web service for launching and managing
local language models. Its product behavior and technical design are being
specified from the beginning.

## Current state

- `main` contains only project documentation.
- No application, deployment workflow, verification scripts, or specifications
  are currently implemented on `main`.

## Agreed constraints for the upcoming specification

- Normal development happens on the local computer using Docker, without
  maintaining project dependencies directly on the macOS host. A native install
  on the current machine is optional and requires explicit interactive approval.
- The intended production target is a Proxmox LXC container reachable by SSH.
  Running Docker inside that LXC is not part of the intended deployment.
- Remote production builds must not require project dependencies or a build on
  macOS. Keep the first deployment workflow simple; its exact steps remain to
  be specified.

## Process

- Discuss and approve a specification before implementing the new application.
- Store specifications in `docs/specs/` when they are written.
- Write project documentation and implementation artifacts in English.
- Keep secrets and runtime data out of source control.
