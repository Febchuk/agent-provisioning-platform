# 03 — Agents, Versions, Deploy, Chat

## Purpose

Let an owner create and version agents, deploy one version, and let teammates chat with it through a share link (API second).

## In scope
- Agent CRUD (no delete needed), templates, version creation, deploy/rollback, file upload, conversations, SSE, share page backend, API keys (P1).

## Out of scope
- Owner auth, per-teammate identity, conversation sharing/export, editing past messages, deleting versions.

## Endpoints

| Method | Path | P | Purpose |
|---|---|---|---|
| GET | `/health` | P0 | `{ok, sandbox_mode}` |
| GET | `/templates` | P0 | `blank`, `data-analyst`, `repo-helper` |
| POST | `/agents` | P0 | `{name, description, template}` → agent + v1 |
| GET | `/agents` | P0 | List with deployed version #, 7-day chat count, new feedback count, latest eval score |
| GET | `/agents/{id}` | P0 | Agent + versions (newest first) |
| POST | `/agents/{id}/versions` | P0 | Create new version from fields (manual edit); `source = manual` |
| POST | `/agents/{id}/files` | P0 | Multipart upload; stored on disk; added to next version created |
| POST | `/agents/{id}/deploy` | P0 | `{version_id}` → moves pointer |
| GET | `/share/{slug}` | P0 | Public agent info: name, description, deployed version number (no config) |
| POST | `/share/{slug}/conversations` | P0 | New conversation on deployed version → `{conversation_id}` |
| POST | `/conversations/{id}/messages` | P0 | `{content}` → `{run_id}`; run starts in background |
| GET | `/runs/{id}/events` | P0 | SSE stream; replays stored events then live ones; ends after `run.done` |
| GET | `/conversations/{id}` | P0 | Messages + run summaries (for reload) |
| GET | `/conversations/{id}/files/{path}` | P1 | Download a file from the sandbox |
| POST | `/agents/{id}/api-keys` | P1 | Returns the key once |
| POST | `/v1/agents/{slug}/messages` | P1 | Bearer key; `{conversation_id?, message}` → waits for completion, returns `{conversation_id, answer, run_id}` |

## Requirements

| ID | P | Requirement |
|---|---|---|
| CD-1 | P0 | WHEN an agent is created from a template, THE SYSTEM SHALL create v1 with that template's prompt, tools and files, and deploy v1 automatically. |
| CD-2 | P0 | WHEN a version is created, THE SYSTEM SHALL copy all unspecified fields from the parent version (`number = max + 1`). |
| CD-3 | P0 | WHEN deploy is called with a version of another agent, THE SYSTEM SHALL return 400. |
| CD-4 | P0 | THE SYSTEM SHALL serve share endpoints without authentication and SHALL NOT return system prompt, guidelines or files from `/share/*`. |
| CD-5 | P0 | WHEN a message is posted, THE SYSTEM SHALL persist the user message before starting the run and return `run_id` within 300 ms. |
| CD-6 | P0 | WHILE a run is in progress for a conversation, IF another message is posted, THEN THE SYSTEM SHALL return 409. |
| CD-7 | P0 | WHEN a client connects to `/runs/{id}/events` after events were emitted, THE SYSTEM SHALL first replay stored events in order (late joiners and reloads see the full trace). |
| CD-8 | P0 | WHEN a run finishes, THE SYSTEM SHALL persist assistant and tool messages so the next turn's history is exact. |
| CD-9 | P1 | THE SYSTEM SHALL store only a sha256 hash of API keys and reject revoked or unknown keys with 401. |
| CD-10 | P0 | Agent slug SHALL be derived from name, unique, URL-safe. |

## Acceptance criteria

- **AC-CD-a** Given a new agent from `data-analyst`, then it has v1 deployed, tools = all five, files include `orders.csv`.
- **AC-CD-b** Given v1 deployed and v2 created, when deploy(v2), then `GET /share/{slug}` reports version 2, and a conversation started before the deploy still runs on v1 (DM-2).
- **AC-CD-c** Given a running turn, when a second message is posted to the same conversation, then 409.
- **AC-CD-d** Given a finished run, when a client opens `/runs/{id}/events`, then it receives every stored event and the stream closes after `run.done`.
- **AC-CD-e** Given `/share/{slug}` response, then it contains no `system_prompt`, `guidelines` or `files` keys.
- **AC-CD-f** (smoke) `scripts/smoke_chat.sh`: create agent → start conversation → ask two dependent questions ("write revenue by month to monthly.csv", then "how many rows are in monthly.csv?") → second answer is correct, proving the sandbox persists across turns.
