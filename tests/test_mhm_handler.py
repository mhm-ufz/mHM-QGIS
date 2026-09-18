"""Tests for selecting the bundled development mHM executable."""
from pathlib import Path

from mhm_qgis.externals import mhm_handler


def test_executable_follows_version():
    assert mhm_handler.executable("5.13").parts[-3:] == (
        "vSpecific", "v5_13", "mhmv5"
    )
    assert mhm_handler.executable("6.0").parts[-3:] == (
        "vSpecific", "v6", "mhmv6"
    )


def test_run_uses_selected_binary(tmp_path: Path):
    calls = []

    assert mhm_handler.run(
        "6", tmp_path,
        lambda command, **options: calls.append((command, options)) or True,
    )
    assert calls == [(
        str(mhm_handler.executable("6")),
        {"cwd": str(tmp_path), "show": True},
    )]
