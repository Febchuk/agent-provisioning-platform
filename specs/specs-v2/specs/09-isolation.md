# 09 — Isolation and Sandboxing

## Purpose

Guarantee that an agent's code execution cannot interfere with other agents, other users' conversations, platform secrets, or the host — and be precise about which guarantees are real and which are acknowledged gaps.

## Key architectural fact

The **agent loop runs in the backend process**, not in the sandbox. The sandbox only executes tool calls (`bash`, file operations). Model calls, API keys, the database and other conversations live outside it. Isolation therefore means: *the sandbox can only see its own workspace and can't reach anything else*.

## Threat model

| # | Threat | Guard | Status |
|---|---|---|---|
| T-1 | Agent A reads/writes agent B's or another conversation's files | One sandbox + one workspace per conversation (IS-3) | P0 |
| T-2 | Teammate opens another teammate's chat | Visitor ownership check (IS-7) | P0 |
| T-3 | Agent reads platform secrets (model key, DB) | Secrets never enter the sandbox; agent loop runs outside (IS-1) | P0 |
| T-4 | Agent escapes to the host | Provider microVM (if spike passes) **or** hardened container (IS-4). A container is not a strong boundary — stated openly | P0 partial |
| T-5 | Agent exfiltrates data / attacks the network | No network by default (IS-3) | P0 |
| T-6 | Noisy neighbor: one agent starves others (CPU, memory, processes, fork bombs) | Per-sandbox limits (IS-3, IS-4); per-agent and global concurrency caps (IS-6) | P0 |
| T-7 | Eval trials contaminate each other | Fresh sandbox per trial, always destroyed (IS-9) | P0 |
| T-8 | Disk exhaustion | Retention + cleanup (IS-10); hard disk quota | P1 / gap |
| T-9 | Prompt injection via uploaded files making the agent misuse tools | Tools are confined to the workspace; no network; owner reviews traces | Gap — acknowledged |

## Backend decision (D-31)

`SANDBOX_BACKEND` = `provider` | `docker` | `local`.

- **T0.4 spike (15 min, first half hour):** with a hosted microVM sandbox provider, create a sandbox, upload `orders.csv`, run `python -c "import pandas"`, write a file, stop/pause, resume, read the file back, destroy.
  - **Spike passes** → `provider` is primary; IS-4 (Docker hardening) is dropped; `local` is the offline demo fallback.
  - **Spike fails or no account is available** → `docker` is primary; provider becomes a documented adapter (P1).
- Record the outcome in `DECISIONS.md` with timings (create, exec round-trip, resume).
- Ask interviewers before T0.4 whether a hosted sandbox is acceptable and whether they have an account.

## Requirements

| ID | P | Requirement |
|---|---|---|
| IS-1 | P0 | THE SYSTEM SHALL NOT pass any platform secret (model API keys, DB path, sandbox-provider key) into a sandbox's environment, files or command line. |
| IS-2 | P0 | THE SYSTEM SHALL select the sandbox implementation from `SANDBOX_BACKEND`, and `GET /health` SHALL report the active backend and whether it is isolated (`provider`/`docker` = true, `local` = false). |
| IS-3 | P0 | Every backend used for chat SHALL provide: a workspace private to one conversation, no outbound network by default, a memory limit, a CPU limit, a process limit, and a per-exec wall-clock timeout. |
| IS-4 | P0 if Docker | Docker containers SHALL run with: `network_mode=none`, `read_only=True` root fs, `tmpfs` for `/tmp`, `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]`, `pids_limit=256`, `mem_limit=512m`, `nano_cpus=1e9`, non-root user `1000:1000`, workspace on a **named volume per conversation**, and never a mount of the Docker socket or any host path. |
| IS-5 | P0 if provider | The provider adapter SHALL implement the same `Sandbox` protocol (02) and map each conversation to one provider sandbox; workspace persistence uses the provider's pause/resume or volume mechanism. |
| IS-6 | P0 | THE SYSTEM SHALL cap live sandboxes globally (`MAX_LIVE_SANDBOXES`, default 20) and concurrent runs per agent (`MAX_RUNS_PER_AGENT`, default 5). IF a chat message would exceed a cap, THEN the API SHALL return 429 with a retry-after and the UI SHALL show "Agent is busy, try again in a moment." Eval trials SHALL queue instead of failing. |
| IS-7 | P0 | THE SYSTEM SHALL only return or accept messages for a conversation when the request's visitor ID matches the conversation's `visitor_id`; otherwise 404 (not 403, to avoid confirming existence). |
| IS-8 | P0 | Agent workspace files SHALL be **copied** into the sandbox at creation, never bind-mounted from the host. |
| IS-9 | P0 | Eval-trial sandboxes SHALL be destroyed in a `finally` block, including on errors and timeouts. |
| IS-10 | P0 | WHILE a conversation sandbox is idle > 15 min, THE SYSTEM SHALL stop it and keep its workspace; workspaces older than `WORKSPACE_RETENTION_DAYS` (default 14) since last activity SHALL be deleted and the conversation marked `files_expired`. |
| IS-11 | P1 | Workspace disk quota (e.g. 1 GB) enforced by the backend. Gap if Docker named volumes are used without a quota driver; documented. |

## Acceptance criteria

- **AC-IS-a** Given any backend, when the agent runs `env` in a sandbox, then the output contains none of `MODEL_API_KEY`, the provider key, or the DB path.
- **AC-IS-b** Given two conversations on the same agent, when conversation A writes `a.txt`, then `list_files` in conversation B does not show it.
- **AC-IS-c** Given visitor V1's conversation, when visitor V2 requests it, then 404.
- **AC-IS-d** Given `MAX_RUNS_PER_AGENT = 1` and a running chat, when a second conversation sends a message, then 429.
- **AC-IS-e** (Docker) `bash: curl https://example.com` fails; `bash: :(){ :|:& };:` is contained by `pids_limit` and the container remains responsive to `docker stop`.
- **AC-IS-f** Given an eval trial whose check raises, then its sandbox no longer exists afterwards.
- **AC-IS-g** Given a conversation idle past the threshold, when the visitor sends a new message, then files written earlier are still present.

## Out of scope

gVisor/Firecracker self-hosting, egress allowlists, per-tenant encryption, prompt-injection defenses beyond tool confinement.
