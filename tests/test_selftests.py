"""Run the modules' built-in __main__ self-tests via subprocess — CI safety net.

Keeps the existing "python storage.py" checks alive inside pytest.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("module", ["storage", "entity_extraction"])
def test_module_selftest(module):
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / f"{module}.py")],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=300,
    )
    assert result.returncode == 0, f"{module} self-test failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"