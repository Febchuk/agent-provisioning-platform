"""Sandbox implementations (specs/02-agent-runtime.md, "Sandbox" section).

`Sandbox` is the Protocol the runner and tools depend on (see
specs/02-agent-runtime.md "Interfaces"). Two implementations:

- `LocalSandbox`: temp dir + subprocess. Used in all unit/runner tests (no
  Docker, no network — specs/07 §1) and as the SB-5 fallback when Docker is
  unavailable at startup.
- `DockerSandbox`: one container per sandbox from the `agentplat-sandbox`
  image (SB-1), `network_mode=none` / `mem_limit=512m` / `nano_cpus=1e9` /
  non-root user (SB-2). Used by integration tests and the smoke script.

Both implementations treat their root as the sandbox's `/workspace` — for
`LocalSandbox` this is a host temp directory that *plays the role of*
`/workspace` (there is no real `/workspace` on the host); for `DockerSandbox`
it is the literal in-container path `/workspace`. Tool code (app/tools.py)
only ever deals in sandbox-relative paths, so this distinction is invisible
above the Sandbox Protocol.
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
    """SB-5 fallback: a host temp directory + subprocess, standing in for a
    container. Used for all deterministic tests (no Docker required).
    """

    def __init__(self) -> None:
        self.id = f"local_{uuid.uuid4().hex[:12]}"
        self._root = Path(tempfile.mkdtemp(prefix="agentplat-sandbox-")).resolve()
        self._destroyed = False

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
    (SB-2). Create/destroy are independent and cheap so a fresh sandbox per
    eval trial (SB-3) is a supported usage pattern — this class only provides
    the primitive; the per-trial orchestration is a later phase.
    """

    def __init__(self) -> None:
        import docker as docker_sdk

        self._docker_sdk = docker_sdk
        self._client = docker_sdk.from_env()
        self.id = f"docker_{uuid.uuid4().hex[:12]}"
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
        )
        self._destroyed = False

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
