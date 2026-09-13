"""Guard the module boundary between ytlounge and yttv.

ytlounge is meant to be extracted into its own distribution one day. That only
stays cheap if nothing in it ever imports yttv or a device library. Without
this test an import across the boundary sneaks in within weeks.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import ytlounge

LOUNGE_ROOT = Path(ytlounge.__file__).parent
FORBIDDEN_PREFIXES = ("yttv", "pychromecast", "pyatv", "zeroconf", "wakeonlan")


def test_importing_ytlounge_pulls_no_device_module() -> None:
    """Import in a fresh interpreter so this test's own imports don't pollute."""
    code = (
        "import sys, ytlounge; "
        "print('\\n'.join(sorted(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    loaded = result.stdout.split()
    offenders = [m for m in loaded if m.split(".")[0] in FORBIDDEN_PREFIXES]
    assert offenders == [], f"ytlounge imported device modules: {offenders}"


def test_ytlounge_sources_contain_no_forbidden_import() -> None:
    """Static check: catches lazy imports inside functions, which the runtime
    check above would miss until that function runs."""
    offenders: list[str] = []
    for path in LOUNGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in FORBIDDEN_PREFIXES:
                    offenders.append(f"{path.relative_to(LOUNGE_ROOT)}:{node.lineno} {name}")
    assert offenders == [], "\n".join(offenders)
