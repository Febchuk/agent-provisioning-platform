# Decisions

Format: date | spec ID | what changed | why

2026-10-02 | SB-5 | `GET /health` returns `sandbox_mode: "not-configured"` instead of one of `"docker"` / `"local-unsafe"` | Sandbox detection logic (Docker availability check) is built in Phase 2 (M1, T1.3); T0.1 only needed the field shape to exist. Will update to real detection when `DockerSandbox`/`LocalSandbox` land.
2026-10-02 | T0.1 | Backend venv created with Homebrew Python 3.11.14, not the system Python 3.9.6 | System Python is too old for current SQLModel/FastAPI; 3.11 also matches the `agentplat-sandbox` image's `python:3.11-slim` base (specs/02-agent-runtime.md SB-1), so dev and sandbox stay aligned.
