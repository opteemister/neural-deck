# Deployment Rules

This document records the deployment requirements for LLM Studio. It defines
the intended workflow without choosing the application process layout or a
specific deployment tool.

## Local development

- Build and run the application in Docker on the developer's computer. The
  local application must not require project runtimes or dependencies to be
  installed directly on macOS.
- The local Docker environment must support the complete application needed
  for development, including the backend and frontend.
- Tests and static checks may run outside Docker when the necessary tools are
  already available. Running them on the host must remain optional.

## Deployment to the server

- The production target is a Linux container on Proxmox, reachable directly
  over SSH. The application runs natively in that container, without Docker.
- Transfer the project from the developer's computer to the server over SSH.
  The transfer must exclude local caches, build output, development data, and
  local secrets.
- Install or update dependencies and build the production application on the
  server. Deployment must not require a production build on the developer's
  computer.
- Provide a simple, repeatable command or script for installation and updates.
  The exact transfer method and server paths are decided in the implementation
  specification.

## Production operation

- Register the application with the server's service manager so it starts
  automatically after a server or container reboot and does not depend on an
  open SSH session.
- Define how every required application process starts and stops. The later
  specification will decide whether the frontend is served by the backend or
  runs as a separate process.
- Keep configuration, secrets, models, logs, and persistent application data
  outside the copied source tree so an update does not overwrite them.
- A deployment must report whether the build and service startup succeeded.
  After startup, check the service state and verify that the application
  responds. Report a failed deployment if either check fails.
- Provide a read-only status command that connects to the server over SSH and
  reports the current service state and application health independently of a
  deployment. It must clearly report SSH or health-check failures.

These rules do not require release directories, archived builds, or a
particular frontend/backend process arrangement.
