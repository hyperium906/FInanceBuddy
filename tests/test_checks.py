"""Runs the standalone regression checks under pytest.

`tests/checks/*.py` are self-asserting scripts covering the Sheets layer,
categorization, wishlist/scraping, and voice. They predate the pytest suite and
are kept as scripts because they read as a narrative of what the layer
guarantees. Each is executed here as a subprocess so `pytest` runs everything;
on failure the script's own output is the failure message.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

CHECKS = sorted((Path(__file__).parent / "checks").glob("check_*.py"))
ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("script", CHECKS, ids=lambda p: p.stem)
def test_check_script_passes(script: Path):
    """Each check script must exit 0 and report all assertions passed."""
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True, text=True, cwd=ROOT, timeout=600,
    )
    if result.returncode != 0:
        pytest.fail(
            f"{script.name} failed (exit {result.returncode})\n\n"
            f"--- stdout ---\n{result.stdout[-4000:]}\n"
            f"--- stderr ---\n{result.stderr[-2000:]}"
        )
    assert "ASSERTIONS PASSED" in result.stdout, result.stdout[-2000:]
