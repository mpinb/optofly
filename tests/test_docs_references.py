"""Guard against documentation drifting away from the code it describes.

Docs are the first thing a new user (or a coding agent) copy-pastes from, so a
snippet importing a symbol that no longer exists costs more than a missing doc
would: it sends them debugging the documentation instead of their problem.
These tests keep the machine-checkable parts of the docs honest.
"""

import importlib
import re
from pathlib import Path

import pytest

DOCS = sorted(Path("docs").glob("*.md")) + [Path("README.md"), Path("CLAUDE.md")]

# `from src.foo.bar import Baz, Qux` at the start of a line inside a code block.
IMPORT_RE = re.compile(r"^from (src[\w.]*) import ([\w, ]+)$", re.MULTILINE)

# `uv run python -m src.tools.foo` / `python -m src.tools.foo`
MODULE_RE = re.compile(r"python -m (src[\w.]+)")

# Paths referenced as `src/...py` or `tests/...py` in prose or commands.
PATH_RE = re.compile(r"(?<![\w/`])((?:src|tests|calibrations|scripts)/[\w/]+\.\w+)")


def _docs_with(pattern):
    """Yield (doc, match) pairs so each finding is its own parametrised case."""
    cases = []
    for doc in DOCS:
        if not doc.exists():
            continue
        for match in pattern.finditer(doc.read_text()):
            cases.append(pytest.param(doc, match, id=f"{doc}:{match.group(1)}"))
    return cases


@pytest.mark.parametrize("doc, match", _docs_with(IMPORT_RE))
def test_documented_imports_resolve(doc, match):
    """Every `from src... import X` in the docs must actually import."""
    module_name, names = match.group(1), match.group(2)
    module = importlib.import_module(module_name)
    for name in (n.strip() for n in names.split(",")):
        assert hasattr(module, name), (
            f"{doc}: documented symbol {module_name}.{name} does not exist"
        )


@pytest.mark.parametrize("doc, match", _docs_with(MODULE_RE))
def test_documented_runnable_modules_exist(doc, match):
    """Every `python -m src.…` command in the docs must name a real module."""
    module_name = match.group(1)
    assert importlib.util.find_spec(module_name) is not None, (
        f"{doc}: documented module {module_name} does not exist"
    )


@pytest.mark.parametrize("doc, match", _docs_with(PATH_RE))
def test_documented_repo_paths_exist(doc, match):
    """Every src//tests//scripts/ path named in the docs must exist."""
    path = Path(match.group(1))
    assert path.exists(), f"{doc}: documented path {path} does not exist"
