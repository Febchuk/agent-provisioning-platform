"""T1.1 — Tools on LocalSandbox (specs/02-agent-runtime.md TL-1..TL-7).

AC-RT-c: path escape -> error string, no file is read.
AC-RT-d: edit_file with 2 occurrences -> error naming the count.
AC-RT-e: bash timeout reported within 3s of a 2s timeout.
Plus TL-7 truncation behavior.
"""
import asyncio
import time

import pytest

from app.sandbox import LocalSandbox
from app.tools import (
    MAX_READ_BYTES,
    MODEL_TRUNCATE_CHARS,
    ToolArgumentError,
    run_tool,
    tool_bash,
    tool_edit_file,
    tool_list_files,
    tool_read_file,
    tool_write_file,
)


@pytest.fixture()
async def sandbox():
    sb = LocalSandbox()
    yield sb
    await sb.destroy()


# --------------------------------------------------------------------------
# TL-1 bash
# --------------------------------------------------------------------------
async def test_bash_runs_command_in_workspace(sandbox):
    out = await tool_bash(sandbox, {"command": "echo hi"}, timeout_s=5)
    assert "hi" in out.model_text
    assert "(exit 0)" in out.model_text


async def test_bash_nonzero_exit_reported(sandbox):
    out = await tool_bash(sandbox, {"command": "exit 7"}, timeout_s=5)
    assert "(exit 7)" in out.model_text


async def test_bash_missing_command_arg_raises():
    sb = LocalSandbox()
    with pytest.raises(ToolArgumentError):
        await tool_bash(sb, {}, timeout_s=5)
    await sb.destroy()


# AC-RT-e
async def test_bash_timeout_reported_within_3s(sandbox):
    start = time.monotonic()
    out = await tool_bash(sandbox, {"command": "sleep 60"}, timeout_s=2)
    elapsed = time.monotonic() - start
    assert elapsed < 3, f"timeout took {elapsed}s, expected < 3s"
    assert "timeout" in out.model_text.lower()


# --------------------------------------------------------------------------
# TL-2 read_file
# --------------------------------------------------------------------------
async def test_read_file_returns_text(sandbox):
    sandbox.seed_file("hello.txt", b"hello world")
    out = await tool_read_file(sandbox, {"path": "hello.txt"})
    assert out.model_text == "hello world"


async def test_read_file_missing_returns_error_string(sandbox):
    out = await tool_read_file(sandbox, {"path": "nope.txt"})
    assert "error" in out.model_text.lower()


async def test_read_file_binary_rejected(sandbox):
    sandbox.seed_file("bin.dat", bytes(range(256)) * 10)
    out = await tool_read_file(sandbox, {"path": "bin.dat"})
    assert "error" in out.model_text.lower()
    assert "binary" in out.model_text.lower()


async def test_read_file_oversized_rejected(sandbox):
    sandbox.seed_file("big.txt", b"a" * (MAX_READ_BYTES + 1))
    out = await tool_read_file(sandbox, {"path": "big.txt"})
    assert "error" in out.model_text.lower()


# AC-RT-c
async def test_read_file_path_escape_returns_error_no_read(sandbox):
    out = await tool_read_file(sandbox, {"path": "../../etc/passwd"})
    assert "error" in out.model_text.lower()
    # No real /etc/passwd content should ever appear (sanity: it would
    # contain "root:" on a unix system).
    assert "root:" not in out.model_text


async def test_read_file_absolute_path_outside_root_rejected(sandbox):
    out = await tool_read_file(sandbox, {"path": "/etc/passwd"})
    assert "error" in out.model_text.lower()


# --------------------------------------------------------------------------
# TL-3 write_file
# --------------------------------------------------------------------------
async def test_write_file_creates_and_overwrites(sandbox):
    out = await tool_write_file(sandbox, {"path": "a.txt", "content": "v1"})
    assert "wrote" in out.model_text
    read_back = await tool_read_file(sandbox, {"path": "a.txt"})
    assert read_back.model_text == "v1"

    await tool_write_file(sandbox, {"path": "a.txt", "content": "v2"})
    read_back2 = await tool_read_file(sandbox, {"path": "a.txt"})
    assert read_back2.model_text == "v2"


async def test_write_file_creates_parent_dirs(sandbox):
    await tool_write_file(sandbox, {"path": "nested/dir/file.txt", "content": "x"})
    read_back = await tool_read_file(sandbox, {"path": "nested/dir/file.txt"})
    assert read_back.model_text == "x"


async def test_write_file_path_escape_rejected(sandbox):
    out = await tool_write_file(sandbox, {"path": "../escape.txt", "content": "x"})
    assert "error" in out.model_text.lower()
    assert not (sandbox.root.parent / "escape.txt").exists()


# --------------------------------------------------------------------------
# TL-4 edit_file
# --------------------------------------------------------------------------
async def test_edit_file_replaces_single_occurrence(sandbox):
    sandbox.seed_file("f.txt", b"hello world")
    out = await tool_edit_file(sandbox, {"path": "f.txt", "old": "world", "new": "there"})
    assert "edited" in out.model_text.lower()
    read_back = await tool_read_file(sandbox, {"path": "f.txt"})
    assert read_back.model_text == "hello there"


# AC-RT-d
async def test_edit_file_two_occurrences_errors_with_count(sandbox):
    sandbox.seed_file("f.txt", b"foo bar foo")
    out = await tool_edit_file(sandbox, {"path": "f.txt", "old": "foo", "new": "baz"})
    assert "2 occurrences" in out.model_text


async def test_edit_file_zero_occurrences_errors_with_count(sandbox):
    sandbox.seed_file("f.txt", b"hello world")
    out = await tool_edit_file(sandbox, {"path": "f.txt", "old": "missing", "new": "x"})
    assert "0 occurrences" in out.model_text


async def test_edit_file_path_escape_rejected(sandbox):
    out = await tool_edit_file(sandbox, {"path": "../../etc/passwd", "old": "root", "new": "x"})
    assert "error" in out.model_text.lower()


# --------------------------------------------------------------------------
# TL-5 list_files
# --------------------------------------------------------------------------
async def test_list_files_recursive(sandbox):
    sandbox.seed_file("a.txt", b"1")
    sandbox.seed_file("sub/b.txt", b"2")
    out = await tool_list_files(sandbox, {"path": "."})
    assert "a.txt" in out.model_text
    assert "sub/b.txt" in out.model_text


async def test_list_files_default_path(sandbox):
    sandbox.seed_file("a.txt", b"1")
    out = await tool_list_files(sandbox, {})
    assert "a.txt" in out.model_text


async def test_list_files_max_entries(sandbox):
    for i in range(520):
        sandbox.seed_file(f"f{i:04d}.txt", b"x")
    out = await tool_list_files(sandbox, {"path": "."})
    assert "truncated" in out.model_text.lower()


async def test_list_files_path_escape_rejected(sandbox):
    out = await tool_list_files(sandbox, {"path": "../../"})
    assert "error" in out.model_text.lower()


# --------------------------------------------------------------------------
# TL-6 path escape guard, all tools, via run_tool dispatch
# --------------------------------------------------------------------------
async def test_run_tool_unknown_tool_raises_argument_error(sandbox):
    with pytest.raises(ToolArgumentError):
        await run_tool("not_a_tool", {}, sandbox, tool_timeout_s=5)


async def test_run_tool_dispatches_each_tool(sandbox):
    out = await run_tool("write_file", {"path": "x.txt", "content": "hi"}, sandbox, tool_timeout_s=5)
    assert "wrote" in out.model_text
    out2 = await run_tool("read_file", {"path": "x.txt"}, sandbox, tool_timeout_s=5)
    assert out2.model_text == "hi"


# --------------------------------------------------------------------------
# TL-7 truncation
# --------------------------------------------------------------------------
async def test_truncation_model_text_capped_with_marker(sandbox):
    big_content = "x" * (MODEL_TRUNCATE_CHARS + 5000)
    sandbox.seed_file("big.txt", big_content.encode())
    out = await tool_read_file(sandbox, {"path": "big.txt"})
    assert out.truncated is True
    assert len(out.model_text) <= MODEL_TRUNCATE_CHARS + len("\n[truncated 5000 chars]")
    assert "[truncated 5000 chars]" in out.model_text
    # Full text kept separately, not truncated to the model limit.
    assert len(out.full_text) == len(big_content)


async def test_truncation_short_output_not_truncated(sandbox):
    sandbox.seed_file("small.txt", b"short")
    out = await tool_read_file(sandbox, {"path": "small.txt"})
    assert out.truncated is False
    assert out.model_text == "short"
