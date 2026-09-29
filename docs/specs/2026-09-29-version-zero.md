# LLM Studio: Version Zero Product Specification

Status: **Draft for discussion**

Date: 2026-09-29

Branch: `codex/version-zero`

This document consolidates the product and technical requirements described by
the operator. It is not approved for implementation. Decisions marked **Open**
must be resolved before the affected work is planned. The intended first-release
boundary is also open; the sections below describe the product target, not a
promise that every capability ships in one iteration.

## 1. Purpose and operating context

LLM Studio is a self-hosted control plane for local language-model inference on
one machine. It helps an operator find installed inference engines and models,
define repeatable launches, run multiple models at once, observe the machine
and engines, and reach available models through one OpenAI-compatible endpoint.

The main production environment is a Linux LXC container on a Proxmox host.
Models, engine environments, and hardware are local to that environment. The
developer works on macOS and normally runs the application in Docker. One
operator is assumed for the initial installation; authentication is deferred.

The neighboring `vllm-studio` repository is a reference for already solved
problems. Its behavior is not automatically a requirement. In particular, LLM
Studio should avoid a launch flow in which UI fields are repeatedly translated
and synchronized with a generated command. The operator prefers to supply the
launch command directly.

## 2. Product concepts

- **Engine:** a supported inference implementation, such as vLLM, llama.cpp,
  or SGLang. The exact initial engine list is open.
- **Engine installation / runtime environment:** a particular executable or
  Python virtual environment for an engine, with its path, detected version,
  and availability. Multiple installations of one engine may coexist.
- **Model artifact:** a local model directory or file, potentially one of
  several quantizations or file variants of a model.
- **Launch profile:** a saved, uniquely named choice of engine environment,
  model, environment variables, and launch command. Its identity anchors
  settings, logs, statistics, and metrics for launches from that profile.
- **Running instance:** an observed inference process and its reachable API,
  port, model identity, and lifecycle state. It may be launched by LLM Studio
  or discovered after an external launch.
- **Model registry:** the currently available instances and models exposed by
  the unified API. Observation of live services must contribute to this view;
  saved launch records alone are insufficient.

These are product concepts, not a prescribed database schema or API format.

## 3. Engines and runtime environments

### Required behavior

1. Discover supported engines already installed in the target environment.
   Show each detected installation, its executable or virtual-environment
   location, version when determinable, and whether it can be used.
2. Support several environments for the same engine at once. The operator can
   select a specific one for a launch profile and switch profiles between an
   established installation and an experimental version without replacing the
   established one.
3. Allow the operator to refresh discovery after installing or changing an
   engine outside LLM Studio. A missing or broken environment must be shown as
   such, not silently replaced with another one.
4. Treat engine-specific detection and capabilities as extensible so more
   engines can be added without redesigning the product objects.

### Desired follow-on capability; release boundary open

- Create or copy an environment, update one selected environment, and run
  operator-specified maintenance commands. These operations must never update
  another environment implicitly. The first-release extent is **Open**.

### Reference to inspect

`vllm-studio/controller/src/modules/engines/runtimes/runtime-targets.ts` and
adjacent runtime probes contain environment discovery approaches. Their search
paths, supported engines, and update behavior need independent review before
reuse.

## 4. Local models and Hugging Face

### Required behavior

1. Configure one or more local model roots. On selection or refresh, scan the
   filesystem and show discovered model directories and standalone files.
2. Reflect files added, removed, or replaced outside LLM Studio. A manually
   downloaded model must appear in the local inventory without having been
   downloaded through the application.
3. Search and inspect models on Hugging Face and download a selected model or
   selected files into the configured storage. Preserve the distinction between
   a repository, a revision, a quantization, and individual artifacts.
4. Reconcile the Hugging Face view with local storage. Mark an artifact as
   present only when the local files support that claim. Show incomplete or
   uncertain matches explicitly instead of reporting a model as downloaded
   merely because a similarly named directory exists.
5. Make available local artifacts selectable by compatible launch profiles.

### Open matching policy

How to identify an arbitrary manually copied file or directory as a specific
Hugging Face repository, revision, and quantization is **Open**. The design must
handle local artifacts with no reliable Hub identity and must not invent one.

### Reference to inspect

`vllm-studio/controller/src/modules/models/model-browser.ts` and
`controller/src/modules/models/routes.ts` contain local scanning and Hugging
Face browsing examples. The operator specifically wants better reconciliation
for manually added files.

## 5. Launch profiles and process lifecycle

### Required behavior

1. Create and edit multiple profiles. Each profile has a unique name used as
   its stable identity for related settings, logs, metrics, and statistics.
2. Select an exact engine environment and a model artifact. Set environment
   variables and provide the command used to launch the engine. Present the
   effective command clearly; avoid a second generated-command source of truth
   that can drift from the operator's command.
3. Start a profile as a managed process on the target machine; observe startup,
   its port and API readiness, exit, and failures. Stop the specific instance
   without stopping unrelated profiles.
4. Support multiple running profiles concurrently, including more than one
   instance of the same engine on distinct ports and instances using different
   GPUs or models. Show conflicts instead of assuming a single global model.
5. Preserve enough launch identity to reconnect logs and statistics to the
   profile after an application restart. The policy for keeping or stopping
   inference processes across that restart is **Open**.

### Open command policy

The command representation, variable substitution, secret handling, working
directory, process ownership, and port selection are **Open**. The design must
not execute an arbitrary transformed command that differs from the one the
operator reviewed.

## 6. Discovering externally launched inference

1. Detect supported engine processes or endpoints that were started outside
   LLM Studio. A process must not disappear from the UI solely because there
   is no saved launch profile or internal launch record.
2. Where an endpoint is found, ask it for the models it actually serves and
   display the observed port, engine, model, and current reachability.
3. Reconcile managed and external observations into one list without duplicate
   entries. The UI must distinguish a process that LLM Studio can control from
   one it only observes.
4. Include suitable reachable external instances in the unified model registry,
   subject to a routing and trust policy still to be decided.
5. Refresh the view at a useful interval or when requested. The exact discovery
   mechanism (process inspection, listening ports, GPU process data, endpoint
   probes, or a combination) is **Open**.

Process names, GPU activity, and a listening port are clues, not proof of which
model is served. Uncertain identity and inaccessible endpoints must be visible.

## 7. Unified OpenAI-compatible API and basic chat

### Unified API

1. Expose one API through which clients can list available models and send
   inference requests to an appropriate running instance.
2. Route requests to the intended model and instance when multiple profiles or
   engines are active. The public naming and collision policy is **Open**.
3. Include externally launched instances that meet the discovery policy.
4. Report backend unavailability, route failures, and unsupported operations
   clearly. The exact OpenAI endpoints and streaming behavior are **Open**.

### Chat page

1. Show the currently available models from the unified API.
2. Let the operator select one and exchange basic text messages with it.
3. Keep the first chat intentionally simple: no browser, tools, agent workflow,
   or other capabilities. Chat-history persistence and streaming are **Open**.

## 8. Dashboard, metrics, logs, and statistics

### Dashboard

1. Provide a separate dashboard page with an overview of system hardware and
   concurrently running instances. Detect local NVIDIA GPUs where available
   and show at least memory use, compute utilization, and temperature when the
   hardware exposes trustworthy values.
2. Show basic per-instance metrics and allow the operator to select a running
   profile or externally observed instance. Managed instances use profile
   identity; external instances need a stable observable identity whose design
   remains **Open**.
3. Show an aggregate view without hiding the fact that several processes and
   models may be active. The first set of graphs and time windows is **Open**.
4. Prefer current, source-backed values. Show unsupported, stale, and failed
   measurements distinctly; never fabricate zero values for missing data.

### Engine metrics architecture

- Define a common interface for engine metrics, with a provider for each
  supported engine. A vLLM provider and an SGLang provider may expose different
  raw data while producing a consistent UI contract where comparable.
- Preserve source, units, collection time, and availability for each metric.
  The exact sources, refresh frequency, and valid cross-engine comparisons are
  **Open**.
- The neighboring project's GPU and engine metric collection is a reference,
  not a validation of accuracy. Relevant examples are under
  `vllm-studio/controller/src/modules/system/`.

### Logs and token usage

- Show logs for managed launches and associate them with the relevant profile
  and running instance. Log access for external processes is **Open**.
- Collect input, output, and total token usage where the source supports it.
  Report usage per profile for managed launches, not only as a system-wide
  total. Define what can be attributed to an external instance separately.
- Distinguish counts observed through the unified proxy from engine counters
  or direct requests that bypass it; coverage may differ.

## 9. Status page and future benchmarks

### Status page

- Provide a separate page reporting the backend state, the unified
  OpenAI-compatible API state, and other important components selected later.
- Make component failures and degraded states visible rather than treating a
  running web process as proof that inference routing works.

### Benchmarks page

- Include a dedicated **Benchmarks** page as a visible placeholder in the
  initial UI. Benchmark execution, context-length and workload matrices,
  quality comparisons, and speed analysis are deferred to later features.

## 10. Frontend and navigation

1. Define a design system before building the feature pages: layout,
   typography, color, spacing, component states, responsive behavior, and data
   presentation rules. Use it consistently across the application.
2. The planned navigation includes Dashboard, Engines / Environments, Models,
   Launch Profiles, Chat, Status, Settings, and Benchmarks. Final grouping and
   labels are **Open**; logs and usage may be within a profile or separate.
3. Encode shareable page state in URL paths and query parameters. Opening or
   refreshing a URL must restore selected tabs, filters, and other meaningful
   view state. The precise URL contract is **Open**.
4. Keep frontend interaction aligned with the backend's observed state;
   changing a filter or selecting a profile must not imply that a process has
   started or that an engine is healthy.

## 11. Backend interfaces and access

- Implement the backend in Python and the frontend in JavaScript or TypeScript.
  A preferred, still undecided serving shape is to build frontend assets and
  let the backend serve both the UI and API as one service.
- Expose an MCP endpoint so agents can inspect and eventually manage the
  system. Its tool surface, transport, authorization boundary, and write
  permissions are **Open**.
- Do not require application authentication for the first local deployment.
  Authentication and multi-user behavior are later extensions. Network
  exposure rules must be decided before enabling unauthenticated process
  control, downloads, or MCP writes beyond a trusted local environment.

## 12. Data, configuration, and migrations

- Use a local persistent database, provisionally SQLite. Its data directory
  must be separate from application code. Mount it from a persistent directory
  in local Docker and keep it outside the deployed project tree on the server.
- Plan database schema changes through Alembic from the first schema onward.
- Make model roots, configuration location, engine-environment search roots,
  log locations, and other machine-specific paths configurable. Exact names,
  defaults, and validation are **Open**.
- Use `.env` for application runtime configuration in local Docker and on the
  server; provide `.env.example` without secrets. Use a separate local
  deployment-parameter file for SSH target and installation defaults. Its
  exact filename, format, and secret-handling policy are **Open**.
- Keep model files, logs, runtime state, and the database outside copied source
  files so deployment cannot overwrite them.

## 13. Development, deployment, and operation

`DEPLOYMENT_RULES.md` is authoritative for the deployment workflow. In brief:

- Normal local development builds and runs the app in Docker. Tests and static
  checks may run on the host if tools are already installed; they must not make
  host-level project dependencies mandatory.
- With a configured remote host, a deploy command transfers the project over
  SSH and builds and installs it on the remote machine. With no remote host,
  the same installation procedure warns that the current machine will change,
  requires an interactive `Y/N`, and proceeds locally only after `Y`.
- The target machine's service manager starts the required application
  processes after reboot. The exact number of services and UI-serving topology
  are **Open**.
- After deployment, check the service state and an application response. A
  separate read-only status command can inspect a remote target over SSH or
  the current machine, using the same target-selection rule.

## 14. Acceptance scenarios for the complete product target

These scenarios make the intended behavior testable. Which scenarios belong to
the first implementation milestone remains **Open**.

1. Two installed versions of one engine are discovered and selectable without
   modifying either installation during discovery.
2. A model file copied manually into the configured root appears locally and,
   when its Hub identity can be verified, appears as present in Hugging Face
   search results. An uncertain match is labeled uncertain.
3. Two profiles launch concurrently on distinct ports. Stopping one leaves the
   other serving requests; both retain separate logs and statistics.
4. A supported inference service launched outside LLM Studio is discovered,
   shown as externally managed, and becomes selectable through the unified API
   when it is reachable and its model identity is known.
5. A request through the unified API reaches the selected instance even when
   two instances serve different models. The chat lists reachable models and
   can exchange a basic text response with one of them.
6. GPU and engine metric failures appear as unavailable or stale. Available
   token usage is attributable to the correct managed profile.
7. A copied URL restores a filtered or selected UI view after refresh.
8. A remote deployment builds on the target, starts the service, checks both
   process and application health, and the service starts again after reboot.
   A status command reports its state over SSH without deploying.

## 15. Decisions required before implementation planning

### Highest priority

1. **First milestone:** Which capabilities must work in version zero, and
   which are only architectural commitments or page placeholders? In
   particular: environment updates, Hugging Face downloads, external-process
   routing, token accounting, MCP writes, and live dashboard metrics.
2. **Initial engines:** Confirm the exact engine names and priority order.
   Some names in the spoken list need confirmation. Is the first milestone
   vLLM and llama.cpp, or a broader set including SGLang and others?
3. **Network boundary:** Who can reach the dashboard, OpenAI-compatible API,
   and MCP endpoint in the first installation? Which interfaces are loopback
   only, LAN, or private-network accessible while authentication is absent?
4. **Process ownership:** If LLM Studio restarts or the LXC reboots, should
   managed model processes restart automatically, survive a web-service
   restart, or remain stopped until explicitly launched?
5. **External processes:** Must the first milestone only observe them, or also
   route requests and count usage? Should LLM Studio ever stop or adopt a
   process it did not launch?

### Product details

6. How should a model be identified for API clients when multiple instances
   serve the same model, and how should port or alias conflicts be resolved?
7. Which OpenAI-compatible endpoints are mandatory initially: model listing,
   chat completions, completions, embeddings, streaming, or others?
8. What local model layouts and Hugging Face artifact types must be recognized
   first? Are private/gated Hub repositories in scope?
9. Must chats persist between browser sessions, or is an in-memory chat enough
   for the initial simple page?
10. Which profile and system metrics are essential on day one, and what
    retention period is required for token statistics and metric history?
11. What design direction should the UI follow? Which screens or elements from
    `vllm-studio` are positive references, and which should be avoided?

### Technical choices to resolve in the implementation specification

12. Confirm the exact deployment-parameter filename mentioned in discussion
    and how it is kept separate from the application's `.env`.
13. Decide whether native installation on macOS after an explicit `Y` is a
    fully supported service installation or a best-effort developer option.
14. Decide whether the backend serves the built SPA, the initial MCP transport,
    the process discovery method, and the shape of profile commands and secrets.
15. Define a development approach for Linux/GPU-specific discovery when the
    normal local Docker environment runs on macOS.

No implementation or migration should start from this draft until the operator
reviews the scope and the highest-priority decisions above.
