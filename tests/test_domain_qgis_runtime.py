"""Run QGIS domain checks in an isolated application process."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_domain_qgis_runtime():
    # Inherit the caller's path so the child finds the same QGIS as the test run.
    paths = (str(Path(__file__).parents[1] / "src"), os.environ.get("PYTHONPATH", ""))
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("qgis_domain_checks.py"))],
        env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                 PYTHONPATH=os.pathsep.join(filter(None, paths))),
        capture_output=True, text=True, timeout=90,
    )
    if result.returncode == 77:
        pytest.skip("Real QGIS is not installed")
    assert result.returncode == 0, result.stdout + result.stderr
