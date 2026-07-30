"""environment.yml must stay in step with pyproject.toml.

The conda path is a second, hand-maintained dependency list with no lockfile,
so nothing stops it drifting -- and it had drifted by five packages, four of
which are imported at module level. A conda user following
getting-started.md Option 2 got an environment where `main.py` died on import.
This test is the CI check that was missing.

environment.yml is parsed with a small hand-rolled reader rather than PyYAML,
which is not a project dependency and would be a silly one to add for one file.
"""

import re
import tomllib
from pathlib import Path

PYPROJECT = Path("pyproject.toml")
ENVIRONMENT = Path("environment.yml")

# Packages that are deliberately absent from environment.yml, with the reason.
# Keep this empty unless there is a real one -- it is an exemption list, and
# every entry is a way for the two files to disagree on purpose.
EXEMPT: dict[str, str] = {}


def _requirement_name(spec: str) -> str:
    """'ximea @ git+https://...' -> 'ximea'; 'numpy>=2.2.5' -> 'numpy'."""
    spec = spec.strip().strip("-").strip()
    spec = spec.split("@")[0]
    return re.split(r"[><=!~\[ ]", spec)[0].strip().lower()


def _pyproject_dependencies() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text())
    return {_requirement_name(d) for d in data["project"]["dependencies"]}


def _environment_packages() -> set[str]:
    """Every package named in environment.yml, conda and pip sections alike."""
    names = set()
    for line in ENVIRONMENT.read_text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("-") or stripped.startswith("- pip:"):
            continue
        name = _requirement_name(stripped)
        if name and name not in {"pip", "python", "defaults", "conda-forge"}:
            names.add(name)
    return names


def test_environment_yml_covers_every_runtime_dependency():
    missing = _pyproject_dependencies() - _environment_packages() - set(EXEMPT)

    assert missing == set(), (
        f"environment.yml is missing {sorted(missing)}. The conda install path "
        f"is documented in docs/getting-started.md; leaving it short of a "
        f"module-level import means `uv run python main.py` works and "
        f"`mamba env create -f environment.yml` produces a broken environment."
    )


def test_environment_yml_names_no_package_pyproject_does_not():
    """Drift in the other direction: a conda-only package nobody declares."""
    dev_only = {"pytest", "ruff", "ipython", "tomli-w", "optofly"}
    extra = _environment_packages() - _pyproject_dependencies() - dev_only

    assert extra == set(), (
        f"environment.yml declares packages pyproject does not: {sorted(extra)}"
    )


def test_git_dependencies_are_declared_under_pip():
    """conda cannot install a git URL; those have to sit in the pip: block."""
    text = ENVIRONMENT.read_text()
    pip_block = text.split("- pip:", 1)[1] if "- pip:" in text else ""

    for dep in _pyproject_dependencies():
        if dep in {"ximea", "optotune-lens"}:
            assert dep in pip_block, f"{dep} is a git install and must be under pip:"
