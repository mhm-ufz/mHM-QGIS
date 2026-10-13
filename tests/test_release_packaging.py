"""Release-artifact checks that require neither QGIS nor project data."""
from __future__ import annotations

import configparser
import io
import subprocess
import sys
import zipfile
from pathlib import Path

from mhm_qgis._version import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_qgis_zip_contains_only_the_plugin(tmp_path):
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "build_qgis_plugin.py"),
            "--output-dir",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    target = tmp_path / f"mhm_qgis-{__version__}.zip"
    assert target.stat().st_size <= 20 * 1024 * 1024

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        assert {
            "mhm_qgis/__init__.py",
            "mhm_qgis/LICENSE",
            "mhm_qgis/COPYING",
            "mhm_qgis/COPYING.LESSER",
            "mhm_qgis/AUTHORS.md",
            "mhm_qgis/README.md",
            "mhm_qgis/metadata.txt",
        } <= names
        assert all(name.startswith("mhm_qgis/") for name in names)
        # the standalone launcher (micromamba runtime, app start-up) is not plugin code
        assert "mhm_qgis/qt/__main__.py" not in names
        assert not any(b"micromamba" in archive.read(name) for name in names if name.endswith(".py"))
        assert not any(
            "__pycache__" in name
            or name.endswith((".ipynb", ".pyc", ".qrc"))
            or Path(name).name in {"mhmv5", "mhmv6", "plugin_upload.py"}
            for name in names
        )
        metadata = configparser.ConfigParser()
        metadata.read_file(io.StringIO(archive.read("mhm_qgis/metadata.txt").decode()))
        assert metadata["general"]["version"] == __version__
        assert metadata["general"]["category"] == "Raster"


def test_plugin_folder_carries_the_repository_license():
    """The plugin folder, zipped as it is, carries the license and the files it refers to."""
    for name in ("LICENSE", "COPYING", "COPYING.LESSER", "AUTHORS.md"):
        assert (ROOT / "src" / "mhm_qgis" / name).read_bytes() == (ROOT / name).read_bytes(), name


def test_runtime_requirement_files_match():
    def requirements(path):
        return {
            line.split("#", 1)[0].strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.split("#", 1)[0].strip()
        }

    assert requirements(ROOT / "requirements.txt") == requirements(
        ROOT / "src" / "mhm_qgis" / "requirements.txt"
    )
    assert not (ROOT / "src" / "mhm_qgis" / "vSpecific" / "v5_13" / "mhmv5").exists()
    assert not (ROOT / "src" / "mhm_qgis" / "vSpecific" / "v6" / "mhmv6").exists()
