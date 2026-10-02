"""v2 SB-6 factory pattern: `create(workspace_ref)` / `resume(workspace_ref)`
against `LocalSandbox` only -- fast, deterministic, no Docker/Modal/network
required (specs/07 §1 "unit" row). Docker/Modal factory behavior is covered
by the `integration`-marked tests (tests/integration/test_docker_sandbox.py,
tests/integration/test_modal_sandbox.py).
"""
import pytest

from app.sandbox import LocalSandbox, PathEscapeError
from app.sandbox_factory import create_sandbox, get_sandbox_backend, is_isolated, resume_sandbox


def test_create_seeds_a_fresh_workspace():
    sb = LocalSandbox.create("ref-1")
    assert sb.workspace_ref == "ref-1"
    assert sb.root.exists()


async def test_create_then_write_then_resume_reads_same_file():
    sb = LocalSandbox.create("ref-2")
    await sb.write("hello.txt", "hi there")

    sb2 = LocalSandbox.resume("ref-2")
    content = await sb2.read("hello.txt")
    assert content == b"hi there"


def test_different_refs_get_different_workspaces():
    sb_a = LocalSandbox.create("ref-a")
    sb_b = LocalSandbox.create("ref-b")
    assert sb_a.root != sb_b.root


def test_resume_without_prior_create_does_not_raise():
    # Documented limitation: resuming a workspace_ref that was never
    # create()'d (or whose temp dir was cleaned) transparently gets a fresh
    # empty dir rather than raising -- LocalSandbox's persistence is weak by
    # design (process-lifetime only), unlike Docker's named volume or
    # Modal's modal.Volume.
    sb = LocalSandbox.resume("never-created-ref")
    assert sb.root.exists()


def test_bare_constructor_still_works_no_arg_back_compat():
    """The pre-existing unit-test suite (test_tools.py, test_runner.py,
    test_checks.py, test_evals.py, test_improver.py) calls `LocalSandbox()`
    directly with no workspace_ref -- this must keep working unchanged.
    """
    sb = LocalSandbox()
    assert sb.id.startswith("local_")
    assert sb.root.exists()


async def test_path_escape_still_rejected_on_created_sandbox():
    sb = LocalSandbox.create("ref-escape")
    with pytest.raises(PathEscapeError):
        await sb.read("../../etc/passwd")


def test_get_sandbox_backend_defaults_to_local(monkeypatch):
    monkeypatch.delenv("SANDBOX_BACKEND", raising=False)
    assert get_sandbox_backend() == "local"


def test_get_sandbox_backend_reads_env(monkeypatch):
    monkeypatch.setenv("SANDBOX_BACKEND", "docker")
    assert get_sandbox_backend() == "docker"
    monkeypatch.setenv("SANDBOX_BACKEND", "provider")
    assert get_sandbox_backend() == "provider"


def test_get_sandbox_backend_unknown_value_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("SANDBOX_BACKEND", "something-bogus")
    assert get_sandbox_backend() == "local"


def test_is_isolated():
    assert is_isolated("provider") is True
    assert is_isolated("docker") is True
    assert is_isolated("local") is False


async def test_create_sandbox_dispatches_to_local_backend():
    sb = await create_sandbox("dispatch-ref-1", backend="local")
    assert isinstance(sb, LocalSandbox)
    assert sb.workspace_ref == "dispatch-ref-1"


async def test_resume_sandbox_dispatches_to_local_backend():
    sb = await create_sandbox("dispatch-ref-2", backend="local")
    await sb.write("f.txt", "data")
    sb2 = await resume_sandbox("dispatch-ref-2", backend="local")
    assert isinstance(sb2, LocalSandbox)
    content = await sb2.read("f.txt")
    assert content == b"data"
