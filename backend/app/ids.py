"""Short random string IDs (specs/01-data-model.md: "IDs are short random
strings (`ag_…`, `v_…`) unless noted").

Prefix convention (not specified exhaustively by `01`, chosen for consistency
with the two examples given there — `ag_` for agents, `v_` for versions):

    ag_  agents            run_  runs              pr_   proposals
    v_   agent_versions    fb_   feedback           pol_  policies (uses agent_id as PK, no own id)
    conv_ conversations    case_ eval_cases         key_  api_keys
    msg_ messages          evr_  eval_runs
                           evres_ eval_results
"""
from __future__ import annotations

import secrets
import string

_ALPHABET = string.ascii_lowercase + string.digits


def new_id(prefix: str, length: int = 12) -> str:
    """Generate a short random id like `ag_7f3k2m9qzxab`."""
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    return f"{prefix}_{suffix}"
