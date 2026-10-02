"""The five agent tools (specs/02-agent-runtime.md, "Requirements — Tools").

Each tool function takes a `Sandbox` plus JSON-decoded arguments and returns
a plain string result (or raises `ToolArgumentError` for bad args, which the
runner — per RT-6 — turns into an error string rather than letting it
propagate). Tools never raise for expected failure modes (path escape,
binary file, bad edit count, timeout): those are all returned as error
strings per TL-2, TL-4, TL-6.

TL-7 truncation: `run_tool` returns a `ToolOutput` with both the
model-facing (<=10,000 char) text and the full text (<=100,000 char, further
hard-capped so a single tool call can't blow up the trace) for the run
trace.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from app.sandbox import PathEscapeError, Sandbox

MAX_READ_BYTES = 200_000  # TL-2
MODEL_TRUNCATE_CHARS = 10_000  # TL-7
TRACE_MAX_CHARS = 100_000  # TL-7
MAX_LIST_ENTRIES = 500  # TL-5

TOOL_NAMES = ["bash", "read_file", "write_file", "edit_file", "list_files"]


class ToolArgumentError(Exception):
    """Raised when a tool's arguments fail validation (RT-6: the runner
    catches this and returns an error string as the tool result instead of
    raising).
    """


@dataclass
class ToolOutput:
    """TL-7: the model sees `model_text` (<=10,000 chars); the trace stores
    `full_text` (<=100,000 chars) plus whether truncation happened.
    """

    model_text: str
    full_text: str
    truncated: bool


def _truncate_for_model(text: str) -> ToolOutput:
    full_text = text[:TRACE_MAX_CHARS]
    if len(text) > MODEL_TRUNCATE_CHARS:
        kept = text[:MODEL_TRUNCATE_CHARS]
        marker = f"\n[truncated {len(text) - MODEL_TRUNCATE_CHARS} chars]"
        return ToolOutput(model_text=kept + marker, full_text=full_text, truncated=True)
    return ToolOutput(model_text=text, full_text=full_text, truncated=False)


def _is_probably_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    # Heuristic: a high proportion of non-text bytes -> binary.
    text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)) - {0x7F})
    nontext = sum(1 for b in data if b not in text_chars)
    return len(data) > 0 and (nontext / len(data)) > 0.30


async def tool_bash(sandbox: Sandbox, args: dict, timeout_s: int) -> ToolOutput:
    command = args.get("command")
    if not isinstance(command, str) or not command:
        raise ToolArgumentError("bash requires a non-empty string 'command' argument")

    result = await sandbox.exec(command, timeout_s)
    combined = result.stdout
    if result.stderr:
        combined = f"{combined}\n[stderr]\n{result.stderr}" if combined else f"[stderr]\n{result.stderr}"
    combined = f"$ {command}\n(exit {result.exit_code})\n{combined}"
    return _truncate_for_model(combined)


async def tool_read_file(sandbox: Sandbox, args: dict) -> ToolOutput:
    path = args.get("path")
    if not isinstance(path, str) or not path:
        raise ToolArgumentError("read_file requires a non-empty string 'path' argument")

    try:
        data = await sandbox.read(path)
    except PathEscapeError as e:
        return _truncate_for_model(f"error: {e}")
    except FileNotFoundError:
        return _truncate_for_model(f"error: file not found: {path}")
    except IsADirectoryError:
        return _truncate_for_model(f"error: {path} is a directory")

    if len(data) > MAX_READ_BYTES:
        return _truncate_for_model(
            f"error: {path} is {len(data)} bytes, exceeds the {MAX_READ_BYTES}-byte read limit"
        )
    if _is_probably_binary(data):
        return _truncate_for_model(f"error: {path} appears to be a binary file; read_file only supports text")

    text = data.decode("utf-8", errors="replace")
    return _truncate_for_model(text)


async def tool_write_file(sandbox: Sandbox, args: dict) -> ToolOutput:
    path = args.get("path")
    content = args.get("content")
    if not isinstance(path, str) or not path:
        raise ToolArgumentError("write_file requires a non-empty string 'path' argument")
    if not isinstance(content, str):
        raise ToolArgumentError("write_file requires a string 'content' argument")

    try:
        await sandbox.write(path, content)
    except PathEscapeError as e:
        return _truncate_for_model(f"error: {e}")

    return _truncate_for_model(f"wrote {len(content)} chars to {path}")


async def tool_edit_file(sandbox: Sandbox, args: dict) -> ToolOutput:
    path = args.get("path")
    old = args.get("old")
    new = args.get("new")
    if not isinstance(path, str) or not path:
        raise ToolArgumentError("edit_file requires a non-empty string 'path' argument")
    if not isinstance(old, str) or old == "":
        raise ToolArgumentError("edit_file requires a non-empty string 'old' argument")
    if not isinstance(new, str):
        raise ToolArgumentError("edit_file requires a string 'new' argument")

    try:
        data = await sandbox.read(path)
    except PathEscapeError as e:
        return _truncate_for_model(f"error: {e}")
    except FileNotFoundError:
        return _truncate_for_model(f"error: file not found: {path}")
    except IsADirectoryError:
        return _truncate_for_model(f"error: {path} is a directory")

    text = data.decode("utf-8", errors="replace")
    count = text.count(old)
    if count != 1:
        return _truncate_for_model(
            f"error: edit_file requires 'old' to match exactly once, found {count} occurrences"
        )

    updated = text.replace(old, new, 1)
    await sandbox.write(path, updated)
    return _truncate_for_model(f"edited {path} (1 occurrence replaced)")


async def tool_list_files(sandbox: Sandbox, args: dict) -> ToolOutput:
    path = args.get("path", ".")
    if not isinstance(path, str):
        raise ToolArgumentError("list_files requires 'path' to be a string if given")

    try:
        entries = await sandbox.list(path)
    except PathEscapeError as e:
        return _truncate_for_model(f"error: {e}")
    except FileNotFoundError:
        return _truncate_for_model(f"error: path not found: {path}")

    truncated_list = len(entries) > MAX_LIST_ENTRIES
    shown = entries[:MAX_LIST_ENTRIES]
    text = "\n".join(shown)
    if truncated_list:
        text += f"\n[truncated: showing {MAX_LIST_ENTRIES} of {len(entries)} entries]"
    return _truncate_for_model(text)


TOOL_DISPATCH = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "list_files": tool_list_files,
}


async def run_tool(name: str, args: dict, sandbox: Sandbox, *, tool_timeout_s: int) -> ToolOutput:
    """Dispatch to the named tool. Raises `ToolArgumentError` for bad args or
    an unknown tool name (RT-6: the runner catches this), never for expected
    sandbox-level failures (those come back as `ToolOutput` error strings).
    """
    if name == "bash":
        return await tool_bash(sandbox, args, tool_timeout_s)
    handler = TOOL_DISPATCH.get(name)
    if handler is None:
        raise ToolArgumentError(f"unknown tool: {name}")
    return await handler(sandbox, args)


# OpenAI-style function-calling schemas for each tool, keyed by name so the
# runner can filter to `version.tools` (RT-9).
TOOL_SCHEMAS: dict[str, dict] = {
    "bash": {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command in /workspace and return its output.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "The shell command to run."}},
                "required": ["command"],
            },
        },
    },
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file's contents from /workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path relative to /workspace."}},
                "required": ["path"],
            },
        },
    },
    "write_file": {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file in /workspace, creating parent directories as needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to /workspace."},
                    "content": {"type": "string", "description": "Full file contents to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    "edit_file": {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exactly one occurrence of `old` with `new` in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to /workspace."},
                    "old": {"type": "string", "description": "Exact text to replace; must occur exactly once."},
                    "new": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old", "new"],
            },
        },
    },
    "list_files": {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Recursively list files under a path in /workspace (max 500 entries).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path relative to /workspace. Defaults to \".\"."}},
                "required": [],
            },
        },
    },
}


def schemas_for(tool_names: list[str]) -> list[dict]:
    """RT-9: only expose tools listed in `version.tools`."""
    return [TOOL_SCHEMAS[name] for name in tool_names if name in TOOL_SCHEMAS]
