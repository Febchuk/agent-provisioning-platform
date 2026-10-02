"""Integration tests: real Modal sandboxes (specs-v2/specs/09-isolation.md
IS-1, IS-5, IS-8; specs-v2/specs/08-design-decisions.md D-31).

Run explicitly with: pytest -m integration tests/integration/test_modal_sandbox.py
(excluded from the default `pytest -q` run via pytest.ini's addopts).

Requires a live Modal account reachable from this machine (the
"brainbase-labs" profile in ~/.modal.toml in this dev environment). These
tests create and destroy real cloud resources (modal.Sandbox + modal.Volume)
-- every test cleans up its own Volume in a `finally` so no orphaned
resources are left on the account (IS-9's "destroyed in a finally" spirit,
applied here to our own test resources, not just eval trials).
"""
import time
import uuid

import pytest

from app.sandbox import ModalSandbox, PathEscapeError, modal_available

pytestmark = pytest.mark.integration


def _delete_workspace_volume(workspace_ref: str) -> None:
    """Best-effort cleanup of the modal.Volume this test's workspace_ref
    created, so repeated test runs don't accumulate orphaned volumes on the
    shared Modal account. Uses the `modal` CLI's volume delete under the
    hood via the SDK's object, swallowing errors (the volume may already be
    gone, or never got created if the test failed before create()).
    """
    import subprocess

    volume_name = f"agentplat-ws-{workspace_ref}"
    try:
        subprocess.run(
            ["python", "-m", "modal", "volume", "delete", volume_name, "-y"],
            capture_output=True,
            timeout=30,
        )
    except Exception:
        pass


@pytest.fixture()
def workspace_ref():
    assert modal_available(), "Modal must be configured (token + package) for integration tests"
    ref = f"test-{uuid.uuid4().hex[:12]}"
    yield ref
    _delete_workspace_volume(ref)


async def test_no_secrets_leak_into_env(workspace_ref):
    """AC-IS-a equivalent: `env` inside a live ModalSandbox contains none of
    the platform secrets (IS-1). We don't literally have MODEL_API_KEY set
    in this process's env during the test run necessarily, so this asserts
    the stronger, always-checkable property: none of the specific env VAR
    NAMES the platform uses for secrets are present, and neither is the
    Modal token id/secret this very process authenticates with.
    """
    from modal.config import config

    sb = await ModalSandbox.create(workspace_ref)
    try:
        result = await sb.exec("env", timeout_s=15)
        assert result.exit_code == 0
        env_output = result.stdout

        for forbidden_name in ("MODEL_API_KEY", "MODEL_BASE_URL", "DATABASE_URL"):
            assert forbidden_name not in env_output, f"{forbidden_name} leaked into sandbox env"

        token_id = config.get("token_id")
        token_secret = config.get("token_secret")
        if token_id:
            assert token_id not in env_output, "Modal token_id leaked into sandbox env"
        if token_secret:
            assert token_secret not in env_output, "Modal token_secret leaked into sandbox env"
    finally:
        await sb.destroy()


async def test_create_write_destroy_resume_read_back_round_trip(workspace_ref):
    """The IS-5 contract: workspace persistence survives the live sandbox
    being destroyed, via the Modal Volume. Also times each phase and prints
    them (captured by pytest -s / recorded manually in DECISIONS.md from a
    real run -- this assertion-only version just proves correctness; see
    scripts/spike_modal_sandbox.py for the authoritative timing run).
    """
    t0 = time.monotonic()
    sb = await ModalSandbox.create(workspace_ref)
    t_create = time.monotonic() - t0

    await sb.write("orders.csv", "order_id,amount\no_1,9.99\n")
    content = await sb.read("orders.csv")
    assert content == b"order_id,amount\no_1,9.99\n"

    t0 = time.monotonic()
    await sb.destroy()
    t_destroy = time.monotonic() - t0

    t0 = time.monotonic()
    sb2 = await ModalSandbox.resume(workspace_ref)
    t_resume = time.monotonic() - t0

    try:
        content2 = await sb2.read("orders.csv")
        assert content2 == b"order_id,amount\no_1,9.99\n", (
            "file written before destroy() was not readable after resume() -- "
            "Volume-backed persistence is broken"
        )
    finally:
        await sb2.destroy()

    print(
        f"\n[test timings] create={t_create:.2f}s destroy={t_destroy:.2f}s "
        f"resume={t_resume:.2f}s"
    )


async def test_files_copied_not_bind_mounted(workspace_ref):
    """IS-8: files are copied into the sandbox via an explicit upload call
    (ModalSandbox.write -> sandbox.filesystem.write_bytes), never a host
    bind-mount -- Modal sandboxes have no host-bind-mount concept at all, so
    this test instead proves the POSITIVE: a file written via `write()`
    really lands in the sandbox's own filesystem (readable back through the
    same API, and listed by `list()`), which is the observable behavior a
    copy-based seed produces.
    """
    sb = await ModalSandbox.create(workspace_ref)
    try:
        await sb.write("orders.csv", "a,b\n1,2\n")
        listing = await sb.list()
        assert "orders.csv" in listing

        # Confirm it's readable from a real shell command too (not just our
        # own read() method), proving it's a genuine file in the sandbox.
        result = await sb.exec("cat orders.csv", timeout_s=10)
        assert result.exit_code == 0
        assert result.stdout == "a,b\n1,2\n"
    finally:
        await sb.destroy()


async def test_path_escape_rejected(workspace_ref):
    """TL-6, reused for the Modal backend: `../../etc/passwd`-style paths
    are rejected before any Modal API call is made.
    """
    sb = await ModalSandbox.create(workspace_ref)
    try:
        with pytest.raises(PathEscapeError):
            await sb.read("../../etc/passwd")
        with pytest.raises(PathEscapeError):
            await sb.write("../../tmp/evil.txt", "x")
        with pytest.raises(PathEscapeError):
            await sb.list("../../")
    finally:
        await sb.destroy()


async def test_pandas_available_matching_docker_image_capabilities(workspace_ref):
    """SB-1: the Modal sandbox's image must provide what the Docker
    agentplat-sandbox image provides (pandas/numpy/matplotlib), so agent
    code that works on one backend works on the other.
    """
    sb = await ModalSandbox.create(workspace_ref)
    try:
        result = await sb.exec(
            "python -c \"import pandas, numpy, matplotlib; print('ok')\"",
            timeout_s=30,
        )
        assert result.exit_code == 0, f"pandas/numpy/matplotlib import failed: {result.stderr}"
        assert "ok" in result.stdout
    finally:
        await sb.destroy()


async def test_no_network_by_default(workspace_ref):
    """IS-3/T-5: no outbound network by default (block_network=True)."""
    sb = await ModalSandbox.create(workspace_ref)
    try:
        result = await sb.exec(
            "python -c \"import urllib.request; urllib.request.urlopen('https://example.com', timeout=5)\"",
            timeout_s=15,
        )
        assert result.exit_code != 0, "network call unexpectedly succeeded -- block_network is not working"
    finally:
        await sb.destroy()
