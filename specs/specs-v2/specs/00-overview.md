# 00 — Overview (v2)

## 1. Problem

Teams want internal agents that (a) run code and work with files, (b) can be used by people other than their creator, and (c) get better over time **in a way the owner can see and trust**. Most tools stop at (a) and (b); improvement is either manual prompt fiddling or an opaque self-rewrite.

## 2. Users

| Persona | Who | Needs |
|---|---|---|
| **Owner** (primary) | Engineer who builds an internal agent | Fast setup; visible traces; evidence that the agent needs work; a measured, reviewable way to improve it; known cost |
| **Teammate** (secondary) | Non-technical colleague | A simple chat with history; a cheap way to say "that was wrong" |

Confirmed with interviewers: engineer builds, teammates use; chat UI first, API second; improvements are proposed as a diff and approved by the owner.

## 3. Goals and success criteria

| ID | Goal | Measured by |
|---|---|---|
| G-1 | A first-time owner can create and deploy an agent quickly | Share link in ≤ 3 min (V-1) |
| G-2 | Teammates use agents without seeing config, and keep their chats | Share page with no login; chat list; reopen and continue |
| G-3 | Agents measurably improve from real usage | Signal → reproduced issue → proposal fixes it on improve **and** benchmark cases |
| G-4 | Improvements are trustworthy | Every proposal shows diff, per-case results, target-axis gain, benchmark generalization, regressions, cost, policy verdict |
| G-5 | Not locked to one model provider | Configurable `base_url`; provider code only in `llm.py` |
| G-6 (**v2**) | Agents can't interfere with each other, other users, secrets or the host | IS-* tests pass; threat model with honest status |
| G-7 (**v2**) | Quality mechanisms justify their cost | Cost per run, per eval, per proposal is measured and gated |

## 4. Scope

### In scope (P0)

- Create from templates; edit prompt, guidelines, tools, files. Immutable versions; deploy pointer; rollback.
- ReAct runner, five tools; sandbox behind one interface; **backend chosen by a 15-min provider spike** (hosted microVM provider, else hardened Docker); `local` offline fallback.
- **Isolation (v2):** secrets never in the sandbox; per-conversation workspace; no network; resource limits; concurrency caps; visitor isolation.
- Multi-turn chat with SSE step events; **chat history per visitor per agent; new chat; reopen old chats; per-turn version with divider (v2)**; workspaces survive idle stop.
- **Usage and cost tracking; `grounded` hallucination check (v2).**
- **Signals → issues → reproduction with synthetic variants → trigger policy (v2).**
- Eval cases with **improve / benchmark split (v2)**; checks `contains`, `numeric` (**v2**), `python_assert`, `llm_judge`, `grounded` (**v2**); N=3 trials.
- Policy: **min target-axis gain, benchmark gate, per-axis regression limits and floors, pinned cases, cost limit (v2)**; deterministic verdict.
- Improver on **one target axis (v2)**; structured ops; literal lint; size budget; **one open proposal, cooldown (v2)**.
- Proposal review; accept / accept & deploy / reject; note required on override.
- Seed script for the demo agent.

### In scope (P1, in order)
1. Ground-truth CSV import into the benchmark.
2. Reviewer model with on/off comparison (QC-12…14).
3. Rate trigger and sampled LLM monitor (SG-14, SG-15).
4. Standalone Versions & Deploy screen; Studio playground.
5. API keys + `/v1` endpoint.
6. Multi-candidate proposals.
7. Open-source model behind the same `base_url`.

### Out of scope

| Item | Why |
|---|---|
| Accounts, owner auth, multi-tenancy, cross-device history | A visitor cookie and a single owner demonstrate the loop |
| Self-hosted gVisor/Firecracker, egress allowlists | Provider spike or hardened Docker; gap stated in the threat model |
| Token streaming | Step events give the same feedback for less code |
| Visual builder, multi-agent orchestration | Not needed for create → deploy → improve |
| Cron/webhook triggers, Slack | Interviewers prioritized chat then API |
| Automatic proposals or promotion | Owner starts and approves (trust; cost) |
| Fine-tuning | Improvement via versioned instructions |
| Postgres, queues, horizontal scale | Listed as "what breaks first" |
| Billing, per-user quotas | Cost is measured, not charged |

## 5. Assumptions

- A-1 An LLM key for an OpenAI-compatible endpoint is provided.
- A-2 (**v2**) Either a sandbox-provider account is available (ask interviewers) or Docker runs on the demo machine.
- A-3 One owner, a handful of teammates, < 100 trials per proposal.
- A-4 Synthetic, deterministic demo dataset.
- A-5 (**v2**) `MODEL_PRICES` are known for the models used; otherwise cost shows "—".

## 6. Constraints

5 hours total. FastAPI + SQLModel/SQLite; Next.js + Tailwind; OpenAI SDK with `base_url`; sandbox provider SDK **or** Docker SDK; pytest + httpx; `FakeLLM`.

## 7. Glossary (v2 additions in bold)

| Term | Meaning |
|---|---|
| Agent | Named container of versions with one deployed version |
| Version | Immutable snapshot: prompt, guidelines, tools, model, limits, files |
| Guidelines | Structured rules (`skills.md` when rendered) the improver edits |
| Turn / Run | One agent response to one user message, possibly many tool steps |
| Trace | Ordered events of a run |
| Trial | One execution of one case on one version |
| Proposal | Candidate version + evals + verdict awaiting the owner |
| **Visitor** | Anonymous browser identity (cookie) that owns chat history |
| **Workspace** | A conversation's files; outlives its sandbox until retention expires |
| **Signal** | One observation that something may be wrong |
| **Issue** | Signals clustered by an underlying lesson |
| **Reproduction** | Running synthetic variants of a flagged question on the deployed version |
| **Improve split** | Cases whose failures the improver may see |
| **Benchmark split** | Cases never shown to the improver; judge generalization |
| **Target axis** | The one axis a proposal tries to improve; others are guardrails |
| **Grounded** | Every number in the answer appears in a tool output |

## 8. Milestones (v2)

| M | Time | Exit gate (`07`) |
|---|---|---|
| M0 | 0:00–0:25 | Scaffold, seed, FakeLLM, `Sandbox` interface, **provider spike → backend decision** |
| M1 | 0:25–1:20 | Runner + tools + usage metering + chosen backend + isolation tests |
| M2 | 1:20–2:00 | Versions/deploy; conversations with visitor history, per-turn version, workspace resume; SSE |
| M3 | 2:00–3:00 | Feedback → signals → issues → reproduction; cases, splits, checks; executor; policy/verdict |
| M4 | 3:00–3:40 | Improver with target axis; proposals; rate limits |
| M5 | 3:40–4:30 | Frontend (share w/ history, issues/evals, improve, agents/studio, conversations) |
| M6 | 4:30–5:00 | V-1 and reliability checks; P1 only if everything passed |

**Rule:** don't start a milestone before the previous gate passes. Decide cuts at **3:00** using the cut list in `tasks.md`.
