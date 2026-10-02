"""MG-2 — the runner (and rest of the codebase) SHALL depend only on the LLM
Protocol; no provider SDK imports (e.g. `import openai`) outside app/llm.py.
"""
import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
PROVIDER_MODULES = {"openai"}


def _imported_modules(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module.split(".")[0]


def test_no_provider_imports_outside_llm():
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        if path.name == "llm.py":
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for mod in _imported_modules(tree):
            if mod in PROVIDER_MODULES:
                offenders.append(f"{path}: imports '{mod}'")
    assert not offenders, "Provider SDK imports found outside llm.py:\n" + "\n".join(offenders)
