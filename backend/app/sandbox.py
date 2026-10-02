"""Sandbox implementations (specs-v2/specs/02-agent-runtime.md "Sandbox"
section, specs-v2/specs/09-isolation.md).

`Sandbox` is the Protocol the runner and tools depend on. Its per-call
methods (`exec`/`read`/`write`/`list`/`destroy`) are UNCHANGED from v1.
What changed in v2 (SB-6) is construction: each backend now exposes

    create(workspace_ref: str) -> Sandbox   # cold start, seeds version.files
    resume(workspace_ref: str) -> Sandbox   # reattach to an existing workspace

`workspace_ref` is an opaque string the caller persists (e.g.
`conversations.workspace_ref`, added in Phase 1) to look up the same
workspace later. `app/sandbox_factory.py` is the only place that picks a
backend from `SANDBOX_BACKEND`; this module just implements the three
backends.

Three implementations:

- `LocalSandbox`: temp dir + subprocess. Used in all unit/runner tests (no
  Docker, no network — specs/07 §1) and as the `local` backend (SB-5,
  `isolated: false`). For back-compat with the large existing unit-test
  suite (test_tools.py, test_runner.py, test_checks.py, test_evals.py,
  test_improver.py), the bare constructor `LocalSandbox()` still works as a
  convenience/back-compat path equivalent to `create()` with a random
  `workspace_ref` — it is not part of the v2 factory contract, just a
  concession to not having to rewrite ~80 pre-existing call sites that don't
  care about workspace persistence at all.
- `DockerSandbox`: one container per sandbox from the `agentplat-sandbox`
  image (SB-1), `network_mode=none` / `mem_limit=512m` / `nano_cpus=1e9` /
  non-root user (SB-2, unchanged — IS-4's extra hardening flags are
  deliberately NOT added per D-31 ["spike passes -> IS-4 is dropped"] and the
  user's confirmed decision to use Modal as primary with Docker as a simple
  fallback). `create()` makes a new container + a new named Docker volume
  keyed by `workspace_ref`, mounted at `/workspace`; `resume()` looks up the
  same named volume and starts a fresh container against it (SB-4: idle
  sandboxes are stopped, not kept running, but their workspace persists).
- `ModalSandbox`: real `modal` Python SDK (v1.6.0) sandboxes-as-microVMs.
  `create()` makes a new `modal.Volume` (keyed by `workspace_ref`) + a new
  `modal.Sandbox` with that volume mounted at `/workspace` and
  `block_network=True`; `resume()` reattaches the same Volume to a freshly
  started Sandbox. See the class docstring below for the exact SDK surface
  used, verified against the live installed package and a real account
  (`brainbase-labs` profile) — not guessed.

All three implementations treat their root as the sandbox's `/workspace` —
for `LocalSandbox` this is a host temp directory that *plays the role of*
`/workspace` (there is no real `/workspace` on the host); for `DockerSandbox`
and `ModalSandbox` it is the literal in-container path `/workspace`. Tool
code (app/tools.py) only ever deals in sandbox-relative paths, so this
distinction is invisible above the Sandbox Protocol.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

DOCKER_IMAGE = "agentplat-sandbox"
CONTAINER_WORKSPACE = "/workspace"

# Stable parent dir for LocalSandbox workspaces, so `resume(workspace_ref)`
# can find the same directory `create(workspace_ref)` made earlier, as long
# as the backend process is still alive and nothing has cleaned /tmp. This is
# NOT real persistence — it's a weak, process-lifetime-only form appropriate
# for local dev/test (SB-5's `isolated: false` backend). A process restart,
# an OS tmp-cleaner, or a `destroy()` call all lose the workspace permanently,
# unlike DockerSandbox's named volume or ModalSandbox's modal.Volume, which
# both survive the live sandbox being torn down.
LOCAL_WORKSPACES_ROOT = Path(tempfile.gettempdir()) / "agentplat-workspaces"


@dataclass
class ExecResult:
    """Result of running a command in a sandbox (specs/02 Sandbox interface)."""

    stdout: str
    stderr: str
    exit_code: int
    truncated: bool
    duration_ms: int


class Sandbox(Protocol):
    id: str

    async def exec(self, cmd: str, timeout_s: int) -> ExecResult: ...
    async def read(self, path: str) -> bytes: ...
    async def write(self, path: str, content: str) -> None: ...
    async def list(self, path: str = ".") -> list[str]: ...
    async def destroy(self) -> None: ...


class PathEscapeError(Exception):
    """Raised when a sandbox-relative path resolves outside the sandbox root
    (TL-6). Caught by app/tools.py and turned into an error string — never
    allowed to propagate as a tool exception.
    """


def _resolve_relative(root: Path, path: str) -> Path:
    """Resolve `path` (sandbox-relative, e.g. "../../etc/passwd" or
    "/workspace/orders.csv" or "orders.csv") against `root`, and raise
    `PathEscapeError` if the result escapes `root` (TL-6).

    Absolute paths are interpreted as relative to the sandbox root if they
    start with the conventional `/workspace` prefix, otherwise treated as a
    (likely malicious or mistaken) absolute filesystem path and rejected the
    same as any other out-of-root path.
    """
    raw = path
    if raw.startswith(CONTAINER_WORKSPACE):
        raw = raw[len(CONTAINER_WORKSPACE) :].lstrip("/")
    elif raw.startswith("/"):
        # Any other absolute path is never inside the sandbox root.
        raw = raw.lstrip("/")

    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise PathEscapeError(f"path {path!r} resolves outside the sandbox root")
    return candidate


class LocalSandbox:
    """`local` backend (SB-5): a host temp directory + subprocess, standing
    in for a container. Used for all deterministic tests (no Docker
    required) and as the offline/no-Docker/no-Modal fallback.

    `__init__()` with no `workspace_ref` keeps working exactly as it did in
    v1 (random throwaway dir, same as `create()` with a fresh random ref) —
    this is the back-compat path the pre-existing unit-test suite uses and
    is NOT part of the v2 factory contract. New code should use `create()`/
    `resume()`.
    """

    def __init__(self, *, workspace_ref: Optional[str] = None, _seed: bool = False) -> None:
        self.id = f"local_{uuid.uuid4().hex[:12]}"
        self.workspace_ref = workspace_ref or uuid.uuid4().hex[:16]
        if workspace_ref is None:
            # No caller-supplied ref: behave exactly like v1's throwaway temp
            # dir (no stable parent, nothing to resume later).
            self._root = Path(tempfile.mkdtemp(prefix="agentplat-sandbox-")).resolve()
        else:
            # Stable path under LOCAL_WORKSPACES_ROOT so a later resume()
            # with the same workspace_ref finds the same directory (see the
            # module-level LOCAL_WORKSPACES_ROOT docstring for the caveats).
            self._root = (LOCAL_WORKSPACES_ROOT / self.workspace_ref).resolve()
            self._root.mkdir(parents=True, exist_ok=True)
        self._destroyed = False

    @classmethod
    def create(cls, workspace_ref: str) -> "LocalSandbox":
        """SB-6 cold start: new workspace dir for this `workspace_ref`.
        Seeding from `version.files` is the caller's job (chat_runtime.py /
        evals.py already do this via `seed_file`, mirroring how `DockerSandbox`
        and `ModalSandbox` are seeded) — this method just creates the empty,
        addressable workspace.
        """
        return cls(workspace_ref=workspace_ref)

    @classmethod
    def resume(cls, workspace_ref: str) -> "LocalSandbox":
        """SB-6 resume: reattach to the same on-disk dir `create()` made,
        without touching its contents (no reseeding). If the process was
        restarted or /tmp was cleaned since, this transparently creates a
        fresh empty dir at the same path instead of raising — documented
        limitation of this backend's weak persistence (see module docstring).
        """
        return cls(workspace_ref=workspace_ref)

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, path: str) -> Path:
        return _resolve_relative(self._root, path)

    def seed_file(self, name: str, content: bytes) -> None:
        """Test/smoke-script helper: write a file into the sandbox root before
        a run starts (stands in for SB-1's "seeded from version.files").
        """
        target = self._root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    async def exec(self, cmd: str, timeout_s: int) -> ExecResult:
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                "-lc",
                cmd,
                cwd=str(self._root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
                exit_code = proc.returncode if proc.returncode is not None else -1
                timed_out = False
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                stdout_b, stderr_b = b"", b""
                exit_code = -1
                timed_out = True
        except FileNotFoundError:
            # bash itself missing — surface as a normal failed exec rather
            # than an unhandled exception.
            duration_ms = int((time.monotonic() - start) * 1000)
            return ExecResult(stdout="", stderr="bash not found", exit_code=127, truncated=False, duration_ms=duration_ms)

        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        if timed_out:
            stderr = (stderr + f"\n[timeout after {timeout_s}s]").strip()
        return ExecResult(stdout=stdout, stderr=stderr, exit_code=exit_code, truncated=False, duration_ms=duration_ms)

    async def read(self, path: str) -> bytes:
        target = self.resolve(path)
        return target.read_bytes()

    async def write(self, path: str, content: str) -> None:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    async def list(self, path: str = ".") -> list[str]:
        target = self.resolve(path)
        entries = []
        for p in sorted(target.rglob("*")):
            entries.append(str(p.relative_to(self._root)))
        return entries

    async def destroy(self) -> None:
        if self._destroyed:
            return
        shutil.rmtree(self._root, ignore_errors=True)
        self._destroyed = True


def docker_available() -> bool:
    """SB-5: detect whether Docker is reachable at startup."""
    try:
        import docker as docker_sdk

        client = docker_sdk.from_env()
        try:
            client.ping()
            return True
        finally:
            client.close()
    except Exception:
        return False


class DockerSandbox:
    """One container per sandbox, from the `agentplat-sandbox` image (SB-1).

    `network_mode=none`, `mem_limit=512m`, `nano_cpus=1e9`, non-root user
    (SB-2 — unchanged from v1; IS-4's extra hardening flags are deliberately
    NOT added here, per D-31's "spike passes -> IS-4 is dropped" and the
    user's confirmed decision to run Modal as primary with Docker as a
    simple fallback). Create/destroy are independent and cheap so a fresh
    sandbox per eval trial (SB-3) is a supported usage pattern.

    v2 (SB-6): the workspace lives on a **named Docker volume** keyed by
    `workspace_ref`, mounted at `/workspace` — not the container's own
    writable layer. `create()` makes a new volume; `resume()` finds the
    existing volume by name and mounts it into a fresh container (the
    container itself is never kept running while idle, per SB-4 — only the
    volume persists). This is still IS-8-compliant: a named volume is
    data Docker manages internally, never a bind-mount of a host path.
    `destroy()` only stops/removes the live container — it does NOT delete
    the named volume (that's a separate, rarer operation this phase doesn't
    implement: full conversation deletion / retention expiry, SB-4/D-38).
    """

    def __init__(self, *, workspace_ref: Optional[str] = None) -> None:
        import docker as docker_sdk

        self._docker_sdk = docker_sdk
        self._client = docker_sdk.from_env()
        self.id = f"docker_{uuid.uuid4().hex[:12]}"
        self.workspace_ref = workspace_ref or uuid.uuid4().hex[:16]
        self._volume_name = f"agentplat-ws-{self.workspace_ref}"

        # IS-8: the workspace is a named Docker volume, never a host bind
        # mount. get_or_create semantics: Docker's create() is idempotent
        # for an existing name (returns the existing volume), which is
        # exactly what `resume()` needs.
        self._client.volumes.create(name=self._volume_name)

        self._container = self._client.containers.run(
            DOCKER_IMAGE,
            command="sleep infinity",
            detach=True,
            network_mode="none",
            mem_limit="512m",
            nano_cpus=1_000_000_000,
            user="sandbox",
            working_dir=CONTAINER_WORKSPACE,
            name=self.id,
            volumes={self._volume_name: {"bind": CONTAINER_WORKSPACE, "mode": "rw"}},
        )
        self._destroyed = False

    @classmethod
    def create(cls, workspace_ref: str) -> "DockerSandbox":
        """SB-6 cold start: new container + new named volume for
        `workspace_ref`. Seeding from `version.files` is the caller's job
        (same as v1 — chat_runtime.py / evals.py call `seed_file` after
        this returns).
        """
        return cls(workspace_ref=workspace_ref)

    @classmethod
    def resume(cls, workspace_ref: str) -> "DockerSandbox":
        """SB-6 resume: fresh container, same named volume (so prior writes
        are still there) — no reseeding from `version.files`.
        """
        return cls(workspace_ref=workspace_ref)

    def seed_file(self, name: str, content: bytes) -> None:
        """Test/smoke-script helper mirroring LocalSandbox.seed_file, using
        `write` under the hood (content must be text-decodable; sufficient
        for the demo's CSV seed files).
        """
        asyncio.get_event_loop()  # no-op; kept for symmetry/documentation
        self._put_file(name, content)

    def _safe_container_path(self, path: str) -> str:
        """Validate `path` against the container's /workspace root the same
        way `_resolve_relative` does for LocalSandbox, but return a container
        path string (we can't `Path.resolve()` inside the container from the
        host) by resolving it purely lexically with PurePosixPath semantics.
        """
        import posixpath

        raw = path
        if raw.startswith(CONTAINER_WORKSPACE):
            raw = raw[len(CONTAINER_WORKSPACE) :].lstrip("/")
        elif raw.startswith("/"):
            raw = raw.lstrip("/")

        normalized = posixpath.normpath(posixpath.join(CONTAINER_WORKSPACE, raw))
        if normalized != CONTAINER_WORKSPACE and not normalized.startswith(CONTAINER_WORKSPACE + "/"):
            raise PathEscapeError(f"path {path!r} resolves outside the sandbox root")
        return normalized

    def _put_file(self, path: str, content: bytes) -> None:
        import io
        import tarfile

        container_path = self._safe_container_path(path)
        arcname = container_path[len(CONTAINER_WORKSPACE) + 1 :] or Path(path).name
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            info = tarfile.TarInfo(name=arcname)
            info.size = len(content)
            info.mtime = int(time.time())
            tar.addfile(info, io.BytesIO(content))
        tar_stream.seek(0)
        parent = posixpath_dirname(container_path)
        self._container.exec_run(f"mkdir -p {parent}", user="root")
        self._container.put_archive(parent, tar_stream.getvalue())

    async def exec(self, cmd: str, timeout_s: int) -> ExecResult:
        return await asyncio.get_event_loop().run_in_executor(None, self._exec_sync, cmd, timeout_s)

    def _exec_sync(self, cmd: str, timeout_s: int) -> ExecResult:
        start = time.monotonic()
        # The docker SDK's exec_run has no native timeout, so we run the
        # command in the background inside the container and poll, killing
        # it if it overruns timeout_s. This keeps AC-RT-e's "reported within
        # 3s of a 2s timeout" bound regardless of container exec overhead.
        wrapped = (
            f"timeout -k 1 {int(timeout_s)} bash -lc {_shell_quote(cmd)}; "
            f"echo __EXIT_CODE__:$?"
        )
        exec_id = self._client.api.exec_create(
            self._container.id,
            ["bash", "-lc", wrapped],
            workdir=CONTAINER_WORKSPACE,
            user="sandbox",
        )["Id"]
        output = self._client.api.exec_start(exec_id, stream=False)
        duration_ms = int((time.monotonic() - start) * 1000)

        text = output.decode("utf-8", errors="replace")
        exit_code = 1
        marker = "__EXIT_CODE__:"
        if marker in text:
            body, _, tail = text.rpartition(marker)
            text = body
            try:
                exit_code = int(tail.strip().splitlines()[0])
            except (ValueError, IndexError):
                exit_code = 1

        timed_out = exit_code == 124  # GNU coreutils `timeout`'s exit code
        stderr = ""
        if timed_out:
            stderr = f"[timeout after {timeout_s}s]"
        return ExecResult(stdout=text, stderr=stderr, exit_code=exit_code, truncated=False, duration_ms=duration_ms)

    async def read(self, path: str) -> bytes:
        return await asyncio.get_event_loop().run_in_executor(None, self._read_sync, path)

    def _read_sync(self, path: str) -> bytes:
        import tarfile
        import io

        container_path = self._safe_container_path(path)
        try:
            stream, _ = self._container.get_archive(container_path)
        except self._docker_sdk.errors.NotFound:
            raise FileNotFoundError(path)
        raw = io.BytesIO()
        for chunk in stream:
            raw.write(chunk)
        raw.seek(0)
        with tarfile.open(fileobj=raw) as tar:
            member = tar.getmembers()[0]
            extracted = tar.extractfile(member)
            if extracted is None:
                raise IsADirectoryError(path)
            return extracted.read()

    async def write(self, path: str, content: str) -> None:
        await asyncio.get_event_loop().run_in_executor(None, self._put_file, path, content.encode("utf-8"))

    async def list(self, path: str = ".") -> list[str]:
        return await asyncio.get_event_loop().run_in_executor(None, self._list_sync, path)

    def _list_sync(self, path: str) -> list[str]:
        container_path = self._safe_container_path(path)
        result = self._container.exec_run(
            ["find", container_path, "-mindepth", "1"],
            user="sandbox",
            workdir=CONTAINER_WORKSPACE,
        )
        text = result.output.decode("utf-8", errors="replace")
        entries = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            rel = line[len(CONTAINER_WORKSPACE) + 1 :] if line.startswith(CONTAINER_WORKSPACE + "/") else line
            entries.append(rel)
        return sorted(entries)

    async def destroy(self) -> None:
        """Stops/removes the LIVE container only — the named volume (the
        workspace) is left intact (SB-4: idle stop keeps the workspace;
        D-38). Deleting the volume itself is a separate, rarer operation
        (full conversation deletion / retention expiry) not implemented in
        this phase.
        """
        if self._destroyed:
            return
        await asyncio.get_event_loop().run_in_executor(None, self._destroy_sync)
        self._destroyed = True

    def _destroy_sync(self) -> None:
        try:
            self._container.remove(force=True)
        except Exception:
            pass
        self._client.close()


def posixpath_dirname(p: str) -> str:
    import posixpath

    d = posixpath.dirname(p)
    return d or CONTAINER_WORKSPACE


def _shell_quote(s: str) -> str:
    import shlex

    return shlex.quote(s)


# ---------------------------------------------------------------------------
# ModalSandbox (v2, IS-5, D-31) — real `modal` Python SDK (v1.6.0, verified
# against the installed package and a live account, "brainbase-labs" profile
# via ~/.modal.toml, NOT guessed).
# ---------------------------------------------------------------------------
MODAL_APP_NAME = "agentplat-sandbox"
MODAL_VOLUME_PREFIX = "agentplat-ws-"
# python:3.11-slim-equivalent + pandas/numpy/matplotlib, matching
# sandbox/Dockerfile's agentplat-sandbox image capabilities (SB-1) so the
# same agent code (bash/read_file/write_file/edit_file/list_files tool
# calls) works unmodified on either backend.
_MODAL_IMAGE_CACHE: "Optional[object]" = None


def _modal_image():
    """`modal.Image.debian_slim(python_version="3.11").pip_install("pandas",
    "numpy", "matplotlib")` — built once per process and cached (Modal image
    builds are themselves content-addressed/cached server-side across
    processes too, but caching the Image object avoids re-describing it on
    every sandbox creation).
    """
    global _MODAL_IMAGE_CACHE
    if _MODAL_IMAGE_CACHE is None:
        import modal

        _MODAL_IMAGE_CACHE = modal.Image.debian_slim(python_version="3.11").pip_install(
            "pandas", "numpy", "matplotlib"
        )
    return _MODAL_IMAGE_CACHE


def modal_available() -> bool:
    """Best-effort "is Modal usable" check: the SDK imports and a token
    profile is configured. Does not make a network call (unlike
    `docker_available()`'s `client.ping()`) because Modal's client
    authenticates lazily on first real RPC; this just rules out the
    "package not installed" / "no token at all" cases cheaply.
    """
    try:
        import modal  # noqa: F401
        from modal.config import config

        return bool(config.get("token_id") and config.get("token_secret"))
    except Exception:
        return False


class ModalSandbox:
    """`provider` backend (IS-5, D-31) — real `modal.Sandbox` microVMs.

    Verified SDK surface (installed package v1.6.0, `backend/.venv/lib/
    python3.11/site-packages/modal/`), exercised live against the
    `brainbase-labs` Modal account (see `scripts/spike_modal_sandbox.py` and
    DECISIONS.md for real timings):

    - `modal.App.lookup(name, create_if_missing=True)` (async: `.lookup.aio`)
      — one shared App namespace for all agentplat sandboxes.
    - `modal.Volume.from_name(name, create_if_missing=True)` — the
      persistence primitive Modal actually offers for this use case (there
      is no separate "pause/resume a sandbox" API in this SDK version;
      `modal.Sandbox` itself has no pause/resume, only `create`/`terminate`/
      `from_id`). So `create()`/`resume()` both make a FRESH `modal.Sandbox`
      and the thing that's actually reused across them is the Volume,
      mounted at `/workspace` — matching IS-5's "workspace persistence uses
      the provider's pause/resume OR volume mechanism" (Modal's real
      mechanism is the volume one).
    - `modal.Sandbox.create(app=..., image=..., volumes={"/workspace": vol},
      block_network=True, cpu=..., memory=..., timeout=..., workdir=
      "/workspace")` (async: `.create.aio`) — creates a long-lived sandbox
      (default command keeps it alive; no `sleep infinity` needed, unlike
      Docker) that `exec()` can be called against repeatedly for its whole
      lifetime, matching the per-conversation multi-exec usage pattern.
      `block_network=True` was verified live to actually block DNS/HTTP
      (`urllib.request.urlopen` raised `URLError: ... Temporary failure in
      name resolution`), satisfying IS-3/T-5/AC-RT-g.
    - `sandbox.exec(*args, timeout=N)` (async: `.exec.aio`) — HAS a native
      timeout parameter (unlike `DockerSandbox`, which has to wrap with
      `timeout -k 1 N bash -lc ...` because `docker-py`'s `exec_run` has
      none). Returns a `ContainerProcess`-like object with `.stdout`/
      `.stderr` (async `StreamReader`s with `.read.aio()`) and `.wait.aio()`
      for the exit code.
    - `sandbox.filesystem.write_bytes.aio(data, remote_path)` /
      `.read_bytes.aio(remote_path)` / `.list_files.aio(remote_path)` — a
      real filesystem API on the running sandbox (no need to shell out to
      `cat`/redirection the way `DockerSandbox` uses tar archives), verified
      live for a write-then-read-back round trip.
    - `sandbox.terminate.aio()` — stops the live sandbox. This phase's
      `destroy()` maps to exactly this and nothing else: it does NOT delete
      the Volume (same "stop the live process, keep the workspace" contract
      as `DockerSandbox.destroy()`, consistent with SB-4/D-38). Deleting a
      Volume is a separate, rarer operation (`modal volume delete` / the
      CLI) not implemented in this phase.

    Deviations/gaps found vs. what the spec assumes, logged here and in
    DECISIONS.md:
    - No native per-sandbox **process-count limit** (`pids_limit` equivalent)
      is exposed by `modal.Sandbox.create()` — IS-3's "a process limit" is
      NOT enforced for this backend. `cpu`/`memory`/network/exec-timeout all
      ARE enforced (verified). Logged as a gap in DECISIONS.md; a fork bomb
      inside a Modal sandbox is bounded by the sandbox's own memory/cpu
      ceiling (eventually OOM-killed) but not by an explicit pid cap the way
      Docker's `pids_limit=256` would be (note: this phase doesn't add that
      Docker flag either, per the D-31 IS-4-dropped decision, so both
      backends currently share this specific gap).
    - `modal.Sandbox` has no secrets/env passed by this code at all (IS-1):
      `create()` below passes no `secrets=` and no `env=` containing any of
      `MODEL_API_KEY`/`MODEL_BASE_URL`/`DATABASE_URL`/Modal's own token — the
      token used to talk to Modal's control plane lives only in this
      backend process's `~/.modal.toml` profile / client, never inside the
      sandbox's environment or filesystem.
    """

    DEFAULT_TIMEOUT_S = 600  # sandbox lifetime ceiling, not the per-exec timeout
    DEFAULT_CPU = 1.0
    DEFAULT_MEMORY_MB = 512

    def __init__(self, *, sandbox, volume, workspace_ref: str) -> None:
        # Not called directly — use ModalSandbox.create()/.resume() (both
        # `async def`, unlike Local/DockerSandbox's sync constructors, since
        # every real Modal SDK call here is natively async).
        self._sandbox = sandbox
        self._volume = volume
        self.workspace_ref = workspace_ref
        self.id = sandbox.object_id
        self._destroyed = False

    @staticmethod
    def _resolve_relative_posix(path: str) -> str:
        """Same guard as `DockerSandbox._safe_container_path` (TL-6): resolve
        `path` against `/workspace` lexically and reject anything that
        escapes it, without ever touching the real filesystem (we're on the
        host; the sandbox's files live remotely over Modal's API).
        """
        import posixpath

        raw = path
        if raw.startswith(CONTAINER_WORKSPACE):
            raw = raw[len(CONTAINER_WORKSPACE) :].lstrip("/")
        elif raw.startswith("/"):
            raw = raw.lstrip("/")

        normalized = posixpath.normpath(posixpath.join(CONTAINER_WORKSPACE, raw))
        if normalized != CONTAINER_WORKSPACE and not normalized.startswith(CONTAINER_WORKSPACE + "/"):
            raise PathEscapeError(f"path {path!r} resolves outside the sandbox root")
        return normalized

    @classmethod
    async def create(cls, workspace_ref: str) -> "ModalSandbox":
        """SB-6 cold start: new `modal.Volume` for `workspace_ref` + a new
        `modal.Sandbox` with it mounted at `/workspace`. Seeding from
        `version.files` is the caller's job (same convention as Local/Docker
        — chat_runtime.py / evals.py call `seed_file`/`write` after this
        returns); IS-8 is satisfied because that seeding goes through this
        class's own `write`/`seed_file`, an explicit upload call, never a
        host bind-mount (Modal sandboxes have no bind-mount-from-host
        concept at all).
        """
        return await cls._start(workspace_ref, create_volume=True)

    @classmethod
    async def resume(cls, workspace_ref: str) -> "ModalSandbox":
        """SB-6 resume: fresh `modal.Sandbox`, same `modal.Volume` (so prior
        writes are still there via Modal's volume reload-on-mount) — no
        reseeding from `version.files`.
        """
        return await cls._start(workspace_ref, create_volume=False)

    @classmethod
    async def _start(cls, workspace_ref: str, *, create_volume: bool) -> "ModalSandbox":
        import modal

        app = await modal.App.lookup.aio(MODAL_APP_NAME, create_if_missing=True)
        volume_name = f"{MODAL_VOLUME_PREFIX}{workspace_ref}"
        # Volume.from_name is a lazy, synchronous reference constructor (it
        # does not itself make an RPC; hydration/creation happens when the
        # volume is actually used, e.g. mounted into Sandbox.create below) --
        # verified live: calling it with `.aio` fails with AttributeError
        # since it has no async variant, unlike App.lookup/Sandbox.create.
        # `create_if_missing=True` in both branches: this is itself
        # get-or-create, so `create` and `resume` only differ in
        # intent/documentation here, not in actual SDK behavior — there is no
        # separate "must already exist" call to make resume() stricter, which
        # mirrors DockerSandbox.resume()'s identical looseness (Docker's
        # volumes.create() is also idempotent/get-or-create).
        volume = modal.Volume.from_name(volume_name, create_if_missing=True)

        # IS-1: no `secrets=`, no `env=` — the sandbox gets zero platform
        # secrets. IS-3: block_network=True (no outbound net), cpu/memory
        # limits set. No native per-exec timeout is passed here; that's
        # supplied per-call to `exec()` instead (see `exec()` below), unlike
        # Docker's manual `timeout -k 1 N` wrapper — Modal's `exec()` has a
        # real `timeout=` kwarg.
        sandbox = await modal.Sandbox.create.aio(
            app=app,
            image=_modal_image(),
            volumes={CONTAINER_WORKSPACE: volume},
            block_network=True,
            cpu=cls.DEFAULT_CPU,
            memory=cls.DEFAULT_MEMORY_MB,
            timeout=cls.DEFAULT_TIMEOUT_S,
            workdir=CONTAINER_WORKSPACE,
        )
        return cls(sandbox=sandbox, volume=volume, workspace_ref=workspace_ref)

    def seed_file(self, name: str, content: bytes) -> None:
        """Test/smoke-script helper mirroring Local/DockerSandbox.seed_file.
        Modal's filesystem API is natively async-only, so this sync wrapper
        runs the coroutine on whatever loop is current — acceptable here
        because this helper is only ever called from sync test/script setup
        code, never from inside a running event loop (mirrors
        DockerSandbox.seed_file's own sync-wrapper convention).
        """
        asyncio.get_event_loop().run_until_complete(self._write_bytes(name, content))

    async def _write_bytes(self, path: str, content: bytes) -> None:
        target = self._resolve_relative_posix(path)
        await self._sandbox.filesystem.write_bytes.aio(content, target)
        # Found live (not in any doc we guessed from): a modal.Volume's
        # writes are NOT visible to a different container mounting the same
        # Volume until `Volume.commit()` is called -- the first spike run
        # without this raised `SandboxFilesystemNotFoundError` on the file
        # written before destroy() when read back after resume(). Committing
        # after every write keeps this backend's observable durability
        # contract equivalent to Docker's named volume (survives
        # destroy-then-resume) rather than silently losing uncommitted
        # writes. See DECISIONS.md for the exact failure and fix.
        await self._volume.commit.aio()

    async def exec(self, cmd: str, timeout_s: int) -> ExecResult:
        start = time.monotonic()
        proc = await self._sandbox.exec.aio("bash", "-lc", cmd, timeout=int(timeout_s))
        stdout = await proc.stdout.read.aio()
        stderr = await proc.stderr.read.aio()
        exit_code = await proc.wait.aio()
        duration_ms = int((time.monotonic() - start) * 1000)

        # Modal's exec(timeout=...) kills the process and the wait() above
        # returns a nonzero (platform-defined) exit code on timeout, but
        # does not itself produce a "[timeout after Ns]" marker the way
        # DockerSandbox's manual `timeout` wrapper does. We approximate the
        # same observable contract (AC-RT-e: "timeout reported") by checking
        # whether the call ran to (near) the requested wall-clock budget and
        # returned a nonzero code.
        timed_out = exit_code != 0 and duration_ms >= int(timeout_s * 1000) - 250
        if timed_out and f"[timeout after {timeout_s}s]" not in stderr:
            stderr = (stderr + f"\n[timeout after {timeout_s}s]").strip()
        return ExecResult(stdout=stdout, stderr=stderr, exit_code=exit_code, truncated=False, duration_ms=duration_ms)

    async def read(self, path: str) -> bytes:
        target = self._resolve_relative_posix(path)
        try:
            return await self._sandbox.filesystem.read_bytes.aio(target)
        except FileNotFoundError:
            raise
        except Exception as e:
            # Modal raises its own SandboxFilesystemError hierarchy; surface
            # "not found" the same way Local/DockerSandbox do (FileNotFoundError)
            # so app/tools.py's existing error handling doesn't need a
            # Modal-specific branch.
            if "not found" in str(e).lower() or "no such file" in str(e).lower():
                raise FileNotFoundError(path) from e
            raise

    async def write(self, path: str, content: str) -> None:
        await self._write_bytes(path, content.encode("utf-8"))

    async def list(self, path: str = ".") -> list[str]:
        target = self._resolve_relative_posix(path)
        entries: list[str] = []

        async def _walk(dir_path: str) -> None:
            infos = await self._sandbox.filesystem.list_files.aio(dir_path)
            for info in infos:
                # modal.types.FileInfo (verified in the installed package's
                # modal/types.py): has .path (str) and .is_dir()/.is_file().
                rel = info.path
                if rel.startswith(CONTAINER_WORKSPACE + "/"):
                    rel = rel[len(CONTAINER_WORKSPACE) + 1 :]
                entries.append(rel)
                if info.is_dir():
                    await _walk(info.path)

        await _walk(target)
        return sorted(entries)

    async def destroy(self) -> None:
        """Stops the LIVE sandbox only (`sandbox.terminate.aio()`) — the
        Volume (the workspace) is left intact, same "stop keeps workspace"
        contract as `DockerSandbox.destroy()` (SB-4/D-38). Deleting the
        Volume is a separate, rarer operation not implemented in this phase.
        """
        if self._destroyed:
            return
        try:
            await self._sandbox.terminate.aio()
        except Exception:
            pass
        self._destroyed = True
