# 08 — Design Decisions

Each decision: **what we chose**, **what else we considered**, **what we gave up**, **why it won**, and **what would make us revisit it**. IDs (`D-n`) are referenced from `DECISIONS.md` during the build.

## The eight to lead with in the walkthrough

| # | Decision | One-line why |
|---|---|---|
| D-1 | Go deep on **Improve**; keep Create/Deploy minimal | The brief rewards depth, and improvement is the part that's hardest to fake |
| D-7 | **One runner** for chat and evals | What we test is exactly what we ship |
| D-12 | **Immutable versions** + deploy pointer | Rollback, diffs and proposals all fall out for free |
| D-15 | Improve through **versioned instructions**, not fine-tuning or memory | Fast, cheap, reviewable, reversible, model-agnostic |
| D-16 | **Propose and approve**, never auto-promote | An agent that silently rewrites itself loses trust |
| D-20 | **Owner-set policy** with per-axis regression limits | Real tradeoffs differ by axis; the human still decides |
| D-22 | Improver is **blind to the tests** | It can't write to a test it can't see |
| D-11 | **Container per conversation**, fresh per eval trial | Multi-turn file work in chat, isolation in evals |

---

## Product

### D-1 Go deep on Improve
- **Chose:** Spend the most time and polish on feedback → evals → proposal → review. Create and Deploy are functional but plain.
- **Considered:** Even coverage of Create / Deploy / Improve / GPU model; a polished no-code agent builder.
- **Gave up:** A thinner Studio, no GPU model in P0, no visual builder.
- **Why it won:** The brief says "go deep rather than wide." Create and Deploy are table stakes every candidate will build. "Agents get better over time" is the vague part where judgment shows, and a measured before/after is a strong live demo.
- **Revisit if:** Interviewers say infra depth (GPU serving, sandboxing) matters more than the improvement loop.

### D-2 Engineer builds, non-technical teammates use *(confirmed by interviewers)*
- **Chose:** Owner screens expose config (prompt, tools, limits). Teammate screen is a bare chat.
- **Considered:** Business users build agents (form-based, no prompt editing).
- **Gave up:** Accessibility for non-technical builders.
- **Why it won:** Matches how Brainbase customers work. It also creates a natural feedback source: teammates are the ones who notice wrong answers.
- **Revisit if:** Target customer shifts to ops teams building their own agents.

### D-3 Demo agent: CSV data analyst
- **Chose:** "Revenue Analyst" over a seeded `orders.csv`.
- **Considered:** Repo/coding agent, support bot, research agent.
- **Gave up:** Breadth; risk it looks like "just pandas."
- **Why it won:** Answers are **objectively checkable**, so improvement is real, not vibes. It works naturally in chat, exercises code execution and files, and the seed data can contain a planted trap (refunded orders) for a reliable demo.
- **Revisit if:** Interviewers want to see the platform handle code changes in a repo; the repo-helper template is the fallback.

### D-4 Chat UI first, API second *(confirmed by interviewers)*
- **Chose:** Share page is the primary deploy surface; API is P1.
- **Considered:** API-first with a thin share page.
- **Gave up:** Frontend time that could have gone to backend depth.
- **Why it won:** The chat is where feedback is captured, and it's what makes the demo visible to a room.
- **Revisit if:** Primary consumers are other services (then API + webhooks first).

---

## Architecture

### D-5 FastAPI backend + thin Next.js frontend
- **Chose:** All agent logic in Python; Next.js only renders and calls the API.
- **Considered:** All Next.js (TypeScript agent loop); all Python (Streamlit/HTMX).
- **Gave up:** Integration time between two stacks (~20–30 min).
- **Why it won:** Python has the sandbox, data and eval ecosystem. Next.js is where I build polished UI fastest, and UX is a judging criterion. Mitigation: backend works end to end via curl before any frontend.
- **Revisit if:** Running behind at 3:15. Then UI collapses to the three demo screens.

### D-6 Hand-written ReAct loop, no agent framework
- **Chose:** ~150-line loop over the OpenAI-compatible chat API.
- **Considered:** LangGraph, OpenAI Agents SDK, Claude Agent SDK.
- **Gave up:** Built-in retries, tool parsing helpers, ready-made tracing.
- **Why it won:** Full control over where events and traces are emitted, which the improvement loop depends on. No abstraction to fight under time pressure, and every line is defensible in Q&A.
- **Revisit if:** We need sub-agents, parallel tool calls, or long-running durable workflows.

### D-7 One runner for chat, API and evals
- **Chose:** Evals replay cases through the production `run_turn`.
- **Considered:** A lightweight eval harness (no sandbox, mocked tools).
- **Gave up:** Eval speed; each trial pays for a container.
- **Why it won:** A separate harness drifts from production. Then a passing eval means nothing.
- **Revisit if:** Eval cost dominates; then cache base-version results rather than fork the runner.

### D-8 SQLite + asyncio background tasks, no queue
- **Chose:** Single process; runs and evals are `asyncio` tasks; SQLite via SQLModel.
- **Considered:** Postgres + Celery/Redis; a hosted queue.
- **Gave up:** Durability of in-flight runs across restarts, write concurrency, horizontal scale.
- **Why it won:** Zero setup in a 5-hour build. The scale story is clear and honest.
- **Revisit if:** More than one server process, or parallel eval trials hit `database is locked`. **This is the first thing to break at scale.**

### D-9 Step-level SSE events, not token streaming
- **Chose:** Emit one event per tool call, tool result and final message; replay stored events on reconnect.
- **Considered:** Token streaming; WebSockets.
- **Gave up:** The "typing" feel of token streaming.
- **Why it won:** One-way, simple, replayable (reloads and late joiners see the full trace). For an agent, *seeing steps* matters more than seeing tokens.
- **Revisit if:** Users complain final answers feel slow on long responses.

### D-10 OpenAI-compatible client with configurable `base_url`
- **Chose:** One `LLM` protocol; provider code only in `llm.py`.
- **Considered:** Native provider SDKs with their specific features.
- **Gave up:** Provider-specific features (prompt caching, extended thinking, native tool formats).
- **Why it won:** Swapping to an open-source model on GPUs (vLLM) becomes a config change. That answers the optional requirement without spending time on it.
- **Revisit if:** A provider feature is needed for quality (add an adapter inside `llm.py`, not in the runner).

---

## Sandbox

### D-11 Docker container per conversation; fresh container per eval trial
> **Partly superseded in v2 by D-31 and D-38.** Per-conversation scoping and fresh-per-trial stay. The backend (provider vs Docker) is now decided by a spike, and the workspace outlives the container.

- **Chose:** Conversation-scoped container, `network=none`, 512 MB, non-root. Eval trials get a new container each.
- **Considered:** Container per run (stateless); hosted sandboxes (E2B, Modal); plain subprocess.
- **Gave up:** Resources held by idle conversations; real security isolation.
- **Why it won:** Multi-turn file work ("now plot that") needs files to persist across turns. Evals need clean state so trials don't contaminate each other. Local Docker is free and has no signup risk on the day.
- **Revisit if:** Untrusted users or multi-tenant use: move to gVisor/Firecracker or a hosted sandbox, add idle reaping (SB-4). **State plainly in the demo: not a security boundary.**

---

## Versioning and data

### D-12 Immutable versions + a deploy pointer
- **Chose:** Every change creates a new version row; deploying moves a pointer.
- **Considered:** A mutable agent config with an audit log.
- **Gave up:** Storage (a row per tweak); a little UX friction ("save as new version").
- **Why it won:** Rollback is one click; diffs are exact; a proposal is just a candidate version; conversations can pin a version. Several features for one simple rule.
- **Revisit if:** Version count explodes; add drafts that only become versions on deploy.

### D-13 Conversations pin the version they started on
> **Superseded in v2 by D-37** (per-turn version with a visible divider).

- **Chose:** A chat keeps its version even after a new deploy.
- **Considered:** Always use the latest deployed version.
- **Gave up:** A teammate mid-conversation doesn't get the fix until they start a new chat.
- **Why it won:** Consistent behavior inside a chat, and traces stay reproducible for evals and debugging.
- **Revisit if:** Conversations become long-lived (days); offer "continue on latest version."

### D-14 Learned guidelines are structured rules, separate from the system prompt
- **Chose:** `guidelines = [{id, section, text, addresses}]`, rendered as `skills.md` into the prompt.
- **Considered:** Improver rewrites the whole prompt; free-text `skills.md`.
- **Gave up:** Expressiveness: some fixes need prompt restructuring.
- **Why it won:** Edits become deterministic operations (add/replace/delete), diffs are clean, each rule records which cases it was added for, and rules can be linted and size-budgeted. The owner's prompt stays the owner's.
- **Revisit if:** Proposals often fail because the fix belongs in the prompt; allow flagged prompt ops.

---

## Improvement loop

### D-15 Improve through versioned instructions, not weights or memory
- **Chose:** The agent improves by changing its guidelines.
- **Considered:** Fine-tuning an open-source model on corrections; a retrieval memory of past corrections; a few-shot example bank.
- **Gave up:** A higher ceiling: instructions can't teach new skills, only better behavior.
- **Why it won:** Minutes instead of hours, cheap, reviewable as a diff, reversible, and works on any model. Memory/retrieval is harder to evaluate and to explain to an owner.
- **Revisit if:** Failures are knowledge-shaped (many specific facts). Next step would be an example bank, still versioned and evaluated the same way.

### D-16 Propose and approve; never auto-promote *(confirmed by interviewers)*
- **Chose:** The system proposes a version; the owner accepts or rejects.
- **Considered:** Auto-promote when the policy passes.
- **Gave up:** Speed: the loop waits on a human.
- **Why it won:** Trust. Evals are small and imperfect; a human sanity check catches what they miss.
- **Revisit if:** Eval sets get large and reliable. Then auto-promote for low-risk axes only, with automatic rollback.

### D-17 Feedback becomes a *drafted* case; a human confirms
- **Chose:** LLM drafts the case from the correction; owner clicks "Add as test case."
- **Considered:** Every thumbs-down becomes a case automatically.
- **Gave up:** Owner effort per feedback item.
- **Why it won:** Feedback is noisy (wrong corrections, unclear asks). Bad cases poison the eval set and then the improver.
- **Revisit if:** Feedback volume is high; then cluster feedback and confirm in batches.

### D-18 Three check types: `contains`, `python_assert`, `llm_judge`
- **Chose:** Objective checks where possible, LLM judge where needed.
- **Considered:** LLM judge only; exact match only.
- **Gave up:** Some complexity (three evaluators).
- **Why it won:** Objective checks are noise-free and cheap. The judge covers properties like "excludes refunds and says so," which is what feedback naturally produces.
- **Revisit if:** Judge disagreements are frequent; add reference solutions and compare values.

### D-19 3 trials per case, pass at ≥2/3, flag flaky cases
- **Chose:** N=3, majority pass, temperature 0 for evals.
- **Considered:** Single run; 5+ trials.
- **Gave up:** 3× eval cost and time.
- **Why it won:** A single run can't tell a regression from noise. Five is too slow for a live demo. Three gives a majority and a flakiness signal.
- **Revisit if:** Flaky rate is high; raise N for flaky cases only.

### D-20 Owner-set policy: min improvement + per-axis regression limits + pinned cases + override with note *(shaped with interviewers)*
- **Chose:** Configurable policy; system gives a verdict; owner can override with a required note.
- **Considered:** Hard "no regressions ever"; average improvement only.
- **Gave up:** Simplicity: more config for the owner.
- **Why it won:** Regressions aren't equal: one format slip is fine, one safety slip isn't. Pinned cases protect what must never break. The override note leaves an audit trail without blocking the human.
- **Revisit if:** Owners never touch the defaults; then ship sensible presets per template.

### D-21 Verdict is a pure, deterministic function
- **Chose:** Plain code computes fixed/regressed/verdict.
- **Considered:** An LLM summarizing whether to accept.
- **Gave up:** Nothing important.
- **Why it won:** Unit-testable, explainable, and identical every time.

### D-22 Improver never sees checks, rubrics or hidden cases
- **Chose:** Improver sees trace, answer and the teammate's correction only.
- **Considered:** Giving it the rubric for more precise fixes.
- **Gave up:** Some fix precision.
- **Why it won:** If it sees the test, it writes to the test. The correction already carries the intent.
- **Revisit if:** Fix rate is too low on cases with no correction text (owner-authored cases); give it a paraphrased intent, still not the check.

### D-23 Improver edits guidelines only, max 3 ops, size budget
- **Chose:** Bounded, structured edits.
- **Considered:** Unbounded rewrites.
- **Gave up:** Ability to fix many failures at once.
- **Why it won:** Small changes are reviewable and limit the blast radius. The budget prevents guidelines from growing into a pile of special cases.
- **Revisit if:** Budget is hit often; add a consolidation proposal (merge rules, zero regressions required).

### D-24 Literal lint is deterministic code, not an LLM critic
- **Chose:** Reject rules containing case-specific numbers, dates, quarters or 5-word phrases from the case.
- **Considered:** An LLM critic asking "is this rule general?"
- **Gave up:** Some false positives (a rule that legitimately mentions a month).
- **Why it won:** Cheap, instant, predictable, explainable in the UI.
- **Revisit if:** False positives annoy owners; add an "allow anyway" toggle per op.

### D-25 Generalization via hidden sibling cases *(P1)*
> **Superseded in v2 by D-33 and D-41** (improve/benchmark split; reproduction variants populate the benchmark).

- **Chose:** Generate 2 same-kind questions per confirmed case; the improver never sees them.
- **Considered:** Train/test split of real cases.
- **Gave up:** Siblings are LLM-written and LLM-judged, so they inherit judge noise.
- **Why it won:** With ~8 real cases there's nothing to split. Siblings are the only way to test "did it learn the lesson or memorize the question."
- **Revisit if:** Real case volume grows; switch to a held-out split of real cases.

### D-26 One candidate per proposal in P0; three candidates in P1
- **Chose:** Single candidate.
- **Considered:** 3 candidates (minimal / consolidating / broader), pick best by policy.
- **Gave up:** Better odds of a passing proposal.
- **Why it won:** 3× eval cost and time; too slow for a live demo.

---

## UX and process

### D-27 Plain-language UI over eval jargon
- **Chose:** "Thumbs down," "Add as test case," "Fixed / Regressed," "Meets your policy."
- **Considered:** Exposing raw eval terminology and metrics.
- **Gave up:** Some precision for power users (they can open traces).
- **Why it won:** A first-time user must be able to improve an agent without knowing what an eval is (UX criterion).

### D-28 Spec-driven build with a deterministic `FakeLLM`
- **Chose:** Specs with IDs and acceptance criteria; tests that don't hit the model.
- **Considered:** Prompting a coding agent from a loose plan.
- **Gave up:** ~20 minutes up front.
- **Why it won:** Keeps AI coding tools in scope, makes "done" checkable, and gives a ready answer to "how do you know it works?"

### D-29 Explicit out-of-scope list
- **Chose:** Name what is not built (auth, multi-tenancy, secure sandbox, token streaming, triggers, fine-tuning, auto-promote).
- **Why it won:** Prevents scope creep mid-build and becomes the "what I cut and why" section of the walkthrough.

### D-30 Open-source GPU model is last *(confirmed by interviewers)*
- **Chose:** Swappable endpoint now; real deployment only if time remains.
- **Gave up:** An infra showcase.
- **Why it won:** The design already isn't locked to a provider (D-10); time is better spent on the improvement loop.

---

## v2 decisions (after the v1 handoff)

These come from four follow-up discussions: isolation, hallucination and cost, measuring improvement, and triggering improvement, plus chat history. The new leads for the walkthrough are **D-33** (benchmark split), **D-40/D-41** (detection with reproduction), and **D-32** (cost must earn its place).

### D-31 Sandbox backend decided by a 15-minute provider spike, behind one interface
- **Chose:** `Sandbox` interface first; spike a hosted microVM provider; use it if the spike passes, else hardened Docker; `local` as offline fallback.
- **Considered:** Docker only (v1); provider only; self-hosted gVisor/Firecracker.
- **Gave up:** 15 minutes of build time; certainty up front.
- **Why it won:** A provider turns isolation from a disclaimer into a guarantee and removes ~30–40 min of hardening/volume work, but adds network dependency, per-call latency and SDK risk. A time-boxed spike decides with evidence, and the interface keeps both paths open.
- **Revisit if:** Demo venue network is unreliable (switch to `local`/Docker) or exec latency makes eval runs too slow.

### D-32 Cost to serve is measured everywhere and gated in the verdict
- **Chose:** Track tokens, cost and latency per run, eval run and proposal; fail a proposal that raises cost beyond a policy limit.
- **Considered:** Ignore cost (v1); hard per-run budgets.
- **Gave up:** A little plumbing; dependence on an accurate price table.
- **Why it won:** Extra cost (reviewers, more orchestration, bigger models) isn't ruled out, but it must be justified by measured quality gains. Putting score change and cost change side by side makes that tradeoff visible and decidable.
- **Revisit if:** Cost becomes a hard budget per tenant (then quotas and routing).

### D-33 Improve / benchmark split replaces hidden siblings
- **Chose:** `improve` cases (improver sees failures) and `benchmark` cases (never seen; judge generalization). Benchmark is filled by creator ground truth and confirmed reproduction variants.
- **Considered:** Hidden siblings per case (v1, D-25); improver sees everything.
- **Gave up:** Some benchmark coverage on axes with no ground truth yet (shown as a warning).
- **Why it won:** It's the standard train/test split, easy to explain, and it lets the creator contribute ground truth directly. The benchmark gate (no drop on the target axis) is what catches overfitting.
- **Revisit if:** Benchmark sets get large; then sample them per proposal to control cost.

### D-34 One target axis per proposal; other axes are guardrails
- **Chose:** Each proposal improves one axis; all others must stay within regression limits and floors.
- **Considered:** Raise all scores at once.
- **Gave up:** Speed when several axes need work.
- **Why it won:** Clear attribution (which edit moved which score), fewer conflicting edits, less overfitting, smaller diffs to review.
- **Revisit if:** Two axes repeatedly share a root cause; allow a combined target.

### D-35 Acceptance rule: target gain + benchmark gate + per-axis limits and floors + cost limit
- **Chose:** Accept when the target axis gains ≥ threshold, the benchmark on that axis doesn't drop, each other axis stays within its regression limit and above its floor, pinned cases hold, and cost stays within limit. The owner can still override with a note.
- **Considered:** Never regress (v1 lean); average gain only.
- **Gave up:** Simplicity.
- **Why it won:** Answers "accept a gain with a small drop elsewhere?" with a rule: yes, if the drop is within that axis's tolerance and above its floor. Safety-type axes get floor 100%.
- **Revisit if:** Owners never change defaults; ship presets per template.

### D-36 Chat history keyed by an anonymous visitor cookie
- **Chose:** `HttpOnly` visitor cookie; each visitor sees only their chats; owner sees all.
- **Considered:** Everyone sees all chats on the link; require login.
- **Gave up:** Cross-device history; history lost when cookies are cleared.
- **Why it won:** Privacy between teammates without building accounts. The limitation is honest and easy to fix with auth later.
- **Revisit if:** Real users; add accounts and migrate visitor ids.

### D-37 Version chosen per turn, with a visible divider (supersedes D-13)
- **Chose:** Each new message runs on the currently deployed version; the transcript shows "Agent updated to vN."
- **Considered:** Pin the conversation to its starting version (v1).
- **Gave up:** Behavior can change mid-chat.
- **Why it won:** With chat history, old chats get reopened; pinning would keep serving bugs the owner already fixed. Each run records its version, so traces stay reproducible, and the divider removes the confusion that motivated D-13. It also makes the demo's final step land in the same chat.
- **Revisit if:** Long, stateful workflows where mid-chat changes are risky; offer "continue on vN" per chat.

### D-38 Workspace outlives the sandbox, with retention
- **Chose:** Stop idle sandboxes; keep the workspace (named volume or provider persistence) for 14 days; flag expiry in the chat.
- **Considered:** Keep sandboxes running; lose files on idle (v1 P1).
- **Gave up:** Disk usage; a retention job.
- **Why it won:** Reopening old chats only makes sense if their files are still there; idle sandboxes can't be kept running at scale.
- **Revisit if:** Disk becomes a constraint; snapshot to object storage.

### D-39 Cheapest hallucination guard first; reviewer only with evidence
- **Chose:** A code-only `grounded` check (every number in the answer must appear in a tool output) as a check type and a live signal. Reviewer model is P1 and must win an on/off eval comparison.
- **Considered:** Reviewer model on every run; prompting alone.
- **Gave up:** Coverage of non-numeric hallucinations in P0.
- **Why it won:** For a data agent the dominant hallucination is an uncomputed number, and catching it costs nothing. A reviewer roughly doubles model cost per turn; it has to prove its value (D-32).
- **Revisit if:** Smaller open-source models hallucinate prose claims; then the reviewer comparison is the first experiment.

### D-40 Separate detection from evaluation: signals → issues
- **Chose:** Every observation is a signal; signals cluster into issues; owners act on issues.
- **Considered:** Each thumbs-down drafts a case (v1).
- **Gave up:** One LLM clustering call per thumbs-down.
- **Why it won:** "Does the live agent need work?" and "Is this change better?" are different questions with different evidence. Issues aggregate repeated reports and runtime failures into one thing to fix.
- **Revisit if:** Clustering errors are common; let owners merge/split issues.

### D-41 Reproduction with synthetic variants is the evidence bar
- **Chose:** On a new issue, generate 3 variants of the flagged question and run the deployed version once on each; ≥ 2 failures = reproduced. Confirmed variants join the benchmark.
- **Considered:** Act on any single thumbs-down; wait for N reports only.
- **Gave up:** Cost of 3 runs per new issue; variants are LLM-written and LLM-judged.
- **Why it won:** One thumbs-down isn't evidence, but waiting for N reports is slow on low-traffic internal agents. Reproduction turns one report into evidence (or rules it out), and the same variants then measure generalization. Synthetic data does double duty.
- **Revisit if:** Variant quality is poor; let owners edit variants before they count.

### D-42 Trigger policy: ready when confirmed and (≥ N signals or reproduced); never auto-start
- **Chose:** `min_signals = 3` or reproduction; owner starts Improve; "Improve anyway" with a note.
- **Considered:** Auto-start proposals when ready; trigger on every thumbs-down.
- **Gave up:** Hands-off improvement.
- **Why it won:** Trigger frequency is a tunable parameter, not a constant, and acting too often overfits. Keeping the start manual also caps cost.
- **Revisit if:** Benchmarks are large and reliable; auto-start (not auto-promote) on ready issues.

### D-43 One open proposal per agent; cooldown between accepted proposals
- **Chose:** 409 while a proposal is open; 24 h cooldown (0 in the demo seed), overridable with a note.
- **Considered:** Unlimited proposals.
- **Gave up:** Throughput.
- **Why it won:** Each change can be attributed and observed in production before the next; limits churn and overfitting.

### D-44 Proactive LLM monitor is opt-in, sampled, and suggestion-only *(P1)*
- **Chose:** Off by default; when on, reviews a sample of live runs and creates `monitor` signals, never cases; its cost is shown.
- **Considered:** Always-on monitor; no proactive detection.
- **Gave up:** Detection of problems nobody reports, by default.
- **Why it won:** It addresses the open question (should an LLM find problems unprompted?) without hiding its cost or false positives; reproduction still decides whether a finding is real.
- **Revisit if:** Silent failures turn out to be common in traces.

### D-45 The agent loop runs outside the sandbox; secrets never enter it
- **Chose:** Model calls, keys and DB live in the backend; the sandbox only executes tools.
- **Considered:** Running the whole agent inside the sandbox.
- **Gave up:** Nothing significant.
- **Why it won:** The sandbox has nothing worth stealing, which makes the strongest isolation guarantee free. Also keeps one runner for chat and evals (D-7).
