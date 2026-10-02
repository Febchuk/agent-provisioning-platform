# 03 — Agents, Versions, Deploy, Chat (v2)

## Purpose

Let an owner create and version agents and deploy one version; let teammates chat with it through a share link, **keep their chat history**, switch between old chats and start new ones (v2). API second.

## In scope
- Agent CRUD (no delete), templates, versions, deploy/rollback, file upload.
- Conversations with **history per visitor per agent** (v2), per-turn version selection (v2), SSE.
- API keys (P1, first on the cut list).

## Out of scope
- Accounts/login, cross-device chat history, sharing a chat with another teammate, editing or branching past messages, deleting versions, chat search.

## Visitor identity (v2)

No accounts. On the first request to any `/share/*` endpoint without a `visitor` cookie, the server sets `visitor=<random 128-bit id>` (`HttpOnly`, `SameSite=Lax`, 1-year expiry). History is scoped to (agent, visitor). Clearing cookies or switching browsers loses the list; this is a stated limitation (D-36).

## Endpoints

| Method | Path | P | Purpose |
|---|---|---|---|
| GET | `/health` | P0 | `{ok, sandbox_backend, isolated}` (IS-2) |
| GET | `/templates` | P0 | `blank`, `data-analyst`, `repo-helper` |
| POST | `/agents` | P0 | `{name, description, template}` → agent + v1 (deployed) |
| GET | `/agents` | P0 | Name, deployed version #, chats (7d), **open issues** (v2), latest eval score |
| GET | `/agents/{id}` | P0 | Agent + versions (newest first) |
| POST | `/agents/{id}/versions` | P0 | New version from edited fields (`source = manual`) |
| POST | `/agents/{id}/files` | P0 | Multipart upload |
| POST | `/agents/{id}/deploy` | P0 | `{version_id}` |
| GET | `/agents/{id}/conversations` | P0 | **v2** Owner view: all conversations, any visitor (title, visitor short id, last activity, run count, 👎 count) |
| GET | `/share/{slug}` | P0 | Public info (no config) |
| GET | `/share/{slug}/conversations` | P0 | **v2** This visitor's conversations, newest activity first |
| POST | `/share/{slug}/conversations` | P0 | New conversation for this visitor → `{conversation_id}` |
| GET | `/share/{slug}/conversations/{cid}` | P0 | **v2** Messages (with per-message `version`), run summaries, `files_expired`; 404 if not this visitor's |
| POST | `/share/{slug}/conversations/{cid}/messages` | P0 | `{content}` → `{run_id}` (path changed v2 so visitor ownership is checked in one place) |
| GET | `/runs/{id}/events` | P0 | SSE; replays stored events then live; closes after `run.done` |
| PATCH | `/share/{slug}/conversations/{cid}` | P1 | Rename |
| DELETE | `/share/{slug}/conversations/{cid}` | P1 | Delete chat and its workspace |
| GET | `/share/{slug}/conversations/{cid}/files/{path}` | P1 | Download a workspace file |
| POST | `/agents/{id}/api-keys` | P1 | Returns key once |
| POST | `/v1/agents/{slug}/messages` | P1 | Bearer key; `{conversation_id?, message}` → waits; `{conversation_id, answer, run_id}` |

## Requirements — agents, versions, deploy

| ID | P | Requirement |
|---|---|---|
| CD-1 | P0 | WHEN an agent is created from a template, THE SYSTEM SHALL create v1 from the template and deploy it. |
| CD-2 | P0 | WHEN a version is created, THE SYSTEM SHALL copy unspecified fields from the parent (`number = max + 1`). |
| CD-3 | P0 | WHEN deploy is called with another agent's version, THE SYSTEM SHALL return 400. |
| CD-4 | P0 | Share endpoints SHALL NOT return system prompt, guidelines or files, and (**v2**) SHALL enforce visitor ownership (IS-7). |
| CD-10 | P0 | Slugs are derived from the name, unique, URL-safe. |

## Requirements — conversations and runs

| ID | P | Requirement |
|---|---|---|
| CD-5 | P0 | WHEN a message is posted, THE SYSTEM SHALL persist it, update `last_message_at`, and return `run_id` within 300 ms; the run executes in the background. |
| CD-6 | P0 | WHILE a run is in progress in a conversation, IF another message is posted to it, THEN 409. Other conversations are unaffected (subject to IS-6 caps → 429). |
| CD-7 | P0 | `/runs/{id}/events` SHALL replay stored events before streaming live ones. |
| CD-8 | P0 | WHEN a run finishes, THE SYSTEM SHALL persist assistant and tool messages so the next turn's history is exact. |
| CD-9 | P1 | API keys are stored as sha256 hashes; unknown or revoked → 401. |

## Requirements — chat history (v2)

| ID | P | Requirement |
|---|---|---|
| CH-1 | P0 | THE SYSTEM SHALL assign and persist a visitor cookie as described above and stamp `visitor_id` on every conversation created through `/share/*`. |
| CH-2 | P0 | `GET /share/{slug}/conversations` SHALL return only conversations with the caller's `visitor_id` for that agent, sorted by `last_message_at` desc, each with `title`, `last_message_at`, `has_thumbs_down`, `running` (bool). |
| CH-3 | P0 | WHEN the first user message is posted, THE SYSTEM SHALL set `title` to its first 60 characters (word-boundary trimmed, `…` appended if cut). No model call. |
| CH-4 | P0 | WHEN a visitor reopens a conversation, THE SYSTEM SHALL return all messages in order with each assistant message's `version` number, so the UI can draw version dividers. |
| CH-5 | P0 | WHEN a message is posted to a reopened conversation, THE SYSTEM SHALL run it on the **currently deployed** version (DM-2) and resume the conversation's workspace (SB-4). |
| CH-6 | P0 | IF the conversation's workspace has expired (IS-10), THEN THE SYSTEM SHALL create a fresh workspace seeded from the current version's files, set `files_expired = true`, and include a system note in the next run's history: "Files from earlier in this chat are no longer available." |
| CH-7 | P0 | WHEN a visitor navigates away from a running conversation and returns, THE UI SHALL reconnect to the in-progress run's events (via CD-7). |

## Acceptance criteria

- **AC-CD-a** New agent from `data-analyst` → v1 deployed, five tools, `orders.csv` present.
- **AC-CD-b** (**changed v2**) Given a conversation that ran on v1, when v2 is deployed and a new message is posted, then the run uses v2 and the trace starts with `version.changed {1 → 2}`.
- **AC-CD-c** Second message to a running conversation → 409.
- **AC-CD-d** Late SSE subscriber receives all stored events; stream closes after `run.done`.
- **AC-CD-e** `/share/*` responses contain no `system_prompt`, `guidelines`, `files`.
- **AC-CH-a** Visitor V1 creates two conversations, V2 creates one; V1's list has exactly V1's two, newest activity first.
- **AC-CH-b** V2 requesting V1's conversation (GET or POST message) → 404.
- **AC-CH-c** Title of "What was total revenue in Q3 for the enterprise segment, excluding test accounts and internal orders?" is ≤ 61 chars and ends with `…`.
- **AC-CH-d** Given a conversation whose sandbox was stopped for idleness, when a new message asks to read `monthly.csv` written earlier, then it succeeds.
- **AC-CH-e** Given an expired workspace, then `files_expired = true` and the next run's history contains the system note.
- **AC-CD-f** (smoke, updated) `scripts/smoke_chat.sh`: create agent → conversation A: write `monthly.csv`, then ask its row count → correct; start conversation B → `monthly.csv` absent; list conversations → A and B; reopen A → ask again → still correct.
