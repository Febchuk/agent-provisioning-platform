# Decisions

Format: date | spec ID | what changed | why

2026-10-02 | SB-5 | `GET /health` returns `sandbox_mode: "not-configured"` instead of one of `"docker"` / `"local-unsafe"` | Sandbox detection logic (Docker availability check) is built in Phase 2 (M1, T1.3); T0.1 only needed the field shape to exist. Will update to real detection when `DockerSandbox`/`LocalSandbox` land.
2026-10-02 | T0.1 | Backend venv created with Homebrew Python 3.11.14, not the system Python 3.9.6 | System Python is too old for current SQLModel/FastAPI; 3.11 also matches the `agentplat-sandbox` image's `python:3.11-slim` base (specs/02-agent-runtime.md SB-1), so dev and sandbox stay aligned.
2026-10-02 | RT interfaces | `run_turn(...)` takes an added `llm: LLM` keyword not listed in specs/02-agent-runtime.md's `Interfaces` signature | The runner must call some `LLM.chat(...)` and the spec's signature has no way to supply one (no `llm` param, and `version` doesn't carry an LLM instance per specs/01-data-model.md); omitting it would force a hidden global/default provider inside the runner, which breaks MG-2's spirit (provider choice stays a caller concern, same as `FakeLLM` vs `OpenAICompatLLM` in tests) and determinism (specs/07 §1). All other parts of the given signature (version, history, sandbox, emit, source) are unchanged.
2026-10-02 | SB-5 | Resolved: `GET /health` now reports real `sandbox_mode` (`"docker"` / `"local-unsafe"`) via `app.sandbox.docker_available()` (Docker daemon ping at startup), replacing the Phase 1 `"not-configured"` placeholder noted in the first SB-5 entry above.
