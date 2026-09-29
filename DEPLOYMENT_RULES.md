# Deployment Rules

This document records the deployment requirements for LLM Studio. It defines
the intended workflow without choosing the application process layout or a
specific deployment tool.

## Local development

- Build and run the application in Docker on the developer's computer. The
  normal local development workflow must not require project runtimes or
  dependencies to be installed directly on macOS.
- The local Docker environment must support the complete application needed
  for development, including the backend and frontend.
- Tests and static checks may run outside Docker when the necessary tools are
  already available. Running them on the host must remain optional.
- An explicitly confirmed native installation on the current machine is an
  optional deployment mode, including when the current machine is macOS.

## Deployment to the server

- The production target is a Linux container on Proxmox, reachable directly
  over SSH. The application runs natively in that container, without Docker.
- Use the same installation and update procedure whether deployment is
  initiated on the developer's computer or from a checkout on the server.
- When a remote host is configured through an environment variable, transfer
  the project over SSH and run that procedure on the remote host. The transfer
  must exclude local caches, build output, development data, and local secrets.
- When no remote host is configured, warn that installation will affect the
  current machine and require an interactive `Y/N` answer before making any
  changes. Continue only after `Y`; `N` or no answer cancels installation.
  This mode runs the same procedure on the current machine without SSH or a
  project transfer, including when launched from a checkout on the server.
- Install or update dependencies and build the application on the selected
  target machine. A remote deployment must not require a production build on
  the developer's computer.
- Provide a simple, repeatable command or script for both modes. The exact
  environment variable, transfer method, and server paths are decided in the
  implementation specification.

## Production operation

- Register the application with the target machine's service manager so it
  starts automatically after a machine or container reboot and does not depend
  on an open SSH session.
- Define how every required application process starts and stops. The later
  specification will decide whether the frontend is served by the backend or
  runs as a separate process.
- Keep configuration, secrets, models, logs, and persistent application data
  outside the copied source tree so an update does not overwrite them.
- A deployment must report whether the build and service startup succeeded.
  After startup, check the service state and verify that the application
  responds. Report a failed deployment if either check fails.
- Provide a read-only status command that uses the same target selection:
  connect over SSH when a remote host is configured, or inspect the current
  machine otherwise. Report the service state and application health
  independently of a deployment, including SSH or health-check failures.

These rules do not require release directories, archived builds, or a
particular frontend/backend process arrangement.
