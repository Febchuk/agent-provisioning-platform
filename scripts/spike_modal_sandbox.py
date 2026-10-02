#!/usr/bin/env python3
"""v2 Phase 2 validation spike -- specs-v2/specs/09-isolation.md's T0.4 spike
task, run for real against the live Modal account (NOT a mocked test).

Creates a real `ModalSandbox` via `app.sandbox.ModalSandbox.create()`, seeds
`orders.csv` (via scripts/seed_demo.py's deterministic generator, same
ground-truth data the rest of the demo uses), runs
`python -c "import pandas"` to confirm the environment matches the
`agentplat-sandbox` Docker image's capabilities (sandbox/Dockerfile:
pandas/numpy/matplotlib), writes a file, destroys the live sandbox handle,
calls `resume()` with the same `workspace_ref`, reads the file back and
confirms it matches, then fully destroys everything INCLUDING the Modal
Volume it created, so running this script never leaves orphaned cloud
resources on the account.

Prints real, measured timings for: create, exec round-trip, write, read,
destroy, resume. These numbers (not estimates) are what's recorded in
DECISIONS.md.

Usage:
    cd backend && source .venv/bin/activate && python ../scripts/spike_modal_sandbox.py
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from seed_demo import generate_rows  # noqa: E402

from app.sandbox import ModalSandbox  # noqa: E402


def _delete_volume(workspace_ref: str) -> None:
    """Delete the modal.Volume this spike run created, so repeated runs
    don't accumulate orphaned cloud resources on the shared account.
    """
    volume_name = f"agentplat-ws-{workspace_ref}"
    result = subprocess.run(
        ["python", "-m", "modal", "volume", "delete", volume_name, "-y"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"  (cleanup warning: could not delete volume {volume_name!r}: {result.stderr.strip()})")
    else:
        print(f"  cleaned up volume {volume_name!r}")


async def main() -> int:
    workspace_ref = f"spike-{uuid.uuid4().hex[:12]}"
    timings: dict[str, float] = {}

    print(f"=== Modal sandbox validation spike (workspace_ref={workspace_ref}) ===\n")

    # 1. Seed data: reuse the deterministic demo generator (in-memory CSV
    # encoding, same field logic as seed_demo.write_csv but without touching
    # disk -- this script needs the bytes, not a file).
    rows = generate_rows()
    import csv
    import io

    buf = io.StringIO(newline="")
    fieldnames = list(rows[0].keys())
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    orders_csv_bytes = buf.getvalue().encode("utf-8")
    print(f"1. Generated orders.csv ({len(rows)} rows, {len(orders_csv_bytes)} bytes) via seed_demo.generate_rows()")

    try:
        # 2. create()
        t0 = time.monotonic()
        sb = await ModalSandbox.create(workspace_ref)
        timings["create_s"] = time.monotonic() - t0
        print(f"2. create() -> sandbox {sb.id} in {timings['create_s']:.2f}s")

        # 3. upload/seed orders.csv
        t0 = time.monotonic()
        await sb.write("orders.csv", orders_csv_bytes.decode("utf-8"))
        timings["upload_s"] = time.monotonic() - t0
        print(f"3. uploaded orders.csv in {timings['upload_s']:.2f}s")

        # 4. exec: confirm pandas available (matches agentplat-sandbox image)
        t0 = time.monotonic()
        result = await sb.exec(
            "python -c \"import pandas, numpy, matplotlib; import pandas as pd; "
            "df = pd.read_csv('orders.csv'); print('rows:', len(df))\"",
            timeout_s=30,
        )
        timings["exec_roundtrip_s"] = time.monotonic() - t0
        print(
            f"4. exec (import pandas + read_csv) in {timings['exec_roundtrip_s']:.2f}s, "
            f"exit={result.exit_code}, stdout={result.stdout.strip()!r}"
        )
        assert result.exit_code == 0, f"pandas exec failed: {result.stderr}"
        assert str(len(rows)) in result.stdout, f"expected {len(rows)} rows in output, got {result.stdout!r}"

        # 5. write a second file (post-analysis artifact)
        t0 = time.monotonic()
        await sb.write("summary.txt", "spike marker: hello from create()")
        timings["write_s"] = time.monotonic() - t0
        print(f"5. wrote summary.txt in {timings['write_s']:.2f}s")

        # 6. destroy the live sandbox (keeps the Volume per SB-4/D-38)
        t0 = time.monotonic()
        await sb.destroy()
        timings["destroy_s"] = time.monotonic() - t0
        print(f"6. destroy() (live sandbox only, Volume kept) in {timings['destroy_s']:.2f}s")

        # 7. resume() with the same workspace_ref
        t0 = time.monotonic()
        sb2 = await ModalSandbox.resume(workspace_ref)
        timings["resume_s"] = time.monotonic() - t0
        print(f"7. resume() -> new sandbox {sb2.id} in {timings['resume_s']:.2f}s")

        # 8. read back summary.txt and orders.csv, confirm match
        t0 = time.monotonic()
        summary_back = await sb2.read("summary.txt")
        orders_back = await sb2.read("orders.csv")
        timings["read_back_s"] = time.monotonic() - t0
        ok = summary_back == b"spike marker: hello from create()" and orders_back == orders_csv_bytes
        print(
            f"8. read-back after resume in {timings['read_back_s']:.2f}s -- "
            f"{'MATCH' if ok else 'MISMATCH (FAIL)'}"
        )
        assert ok, "resumed workspace did not contain the files written before destroy()"

        # 9. fully destroy
        t0 = time.monotonic()
        await sb2.destroy()
        timings["final_destroy_s"] = time.monotonic() - t0
        print(f"9. final destroy() in {timings['final_destroy_s']:.2f}s")

        print("\n=== SPIKE PASSED ===")
        print("Real timings (seconds):")
        for k, v in timings.items():
            print(f"  {k}: {v:.3f}")
        return 0

    finally:
        # Clean up the Volume so this script never leaves orphaned cloud
        # resources, regardless of pass/fail.
        _delete_volume(workspace_ref)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
