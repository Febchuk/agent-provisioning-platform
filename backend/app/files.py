"""On-disk storage for agent version files (specs/03-chat-and-deploy.md:
`POST /agents/{id}/files`, and `AgentVersion.files` per specs/01-data-model.md:
`[{name, path_on_disk, size}]`).

Files live under `backend/data/files/{agent_id}/{uuid}_{name}` — under the
same gitignored `backend/data/` directory as the SQLite DB (see `.gitignore`:
`backend/data/`). Each agent gets its own subdirectory to keep uploads from
different agents from colliding on filename.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.db import DATA_DIR

FILES_DIR = DATA_DIR / "files"


def store_file(agent_id: str, name: str, content: bytes) -> dict:
    """Write `content` to disk under this agent's upload directory and return
    the `{name, path_on_disk, size}` shape stored in `AgentVersion.files`.
    """
    agent_dir = FILES_DIR / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    disk_name = f"{uuid.uuid4().hex}_{name}"
    path_on_disk = agent_dir / disk_name
    path_on_disk.write_bytes(content)
    return {"name": name, "path_on_disk": str(path_on_disk), "size": len(content)}


def store_template_file(agent_id: str, name: str, text: str) -> dict:
    """Same as `store_file`, for a template's starting text file (CD-1)."""
    return store_file(agent_id, name, text.encode("utf-8"))


def read_file_bytes(path_on_disk: str) -> bytes:
    return Path(path_on_disk).read_bytes()
