"""The plugin's dependency check compares versions and markers, not just imports."""
from __future__ import annotations

import os
import sys

import pytest

from mhm_qgis import dependency_bootstrap as bootstrap

pytest.importorskip("packaging")


def test_an_installed_but_old_version_counts_as_missing():
    assert bootstrap.can_import(bootstrap.dependency_from_requirement_line("pytest>=1"))
    assert not bootstrap.can_import(bootstrap.dependency_from_requirement_line("pytest>=999"))


def test_a_line_for_another_python_is_skipped():
    assert bootstrap.dependency_from_requirement_line('pytest; python_version < "3"') is None
    assert bootstrap.dependency_from_requirement_line('pytest; python_version >= "3"') is not None


def test_qgis_numpy_is_reported_never_replaced(monkeypatch):
    ran = []
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *args, **kwargs: ran.append(args))
    for name in ("create_progress_dialog", "show_failure", "show_info", "set_wait_cursor"):
        monkeypatch.setattr(bootstrap, name, lambda *_args, **_kwargs: None)
    result = bootstrap.install_missing_dependencies(None, [bootstrap.dependency_from_requirement_line("numpy>=999")])
    assert not ran
    assert "newer QGIS" in result.failed["numpy>=999"]


def test_every_install_holds_the_numpy_qgis_has(monkeypatch):
    monkeypatch.setattr(bootstrap.importlib.metadata, "version", lambda name: {"numpy": "1.26.4"}[name])
    assert bootstrap.pip_install_command("rioxarray>=0.14")[-3:] == ["--user", "rioxarray>=0.14", "numpy==1.26.4"]


def test_qgiss_python_is_run_not_the_qgis_binary(tmp_path, monkeypatch):
    assert bootstrap.python_executable() == sys.executable  # already a Python
    macos = tmp_path / "QGIS.app" / "Contents" / "MacOS"
    (macos / "bin").mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(macos / "QGIS"))
    monkeypatch.setattr(sys, "exec_prefix", str(tmp_path / "none"))
    with pytest.raises(RuntimeError, match="Could not find the Python of this QGIS"):
        bootstrap.python_executable()
    (macos / "bin" / "python3").touch()
    assert bootstrap.python_executable() == str(macos / "bin" / "python3")
    assert bootstrap.pip_install_command("scipy")[0] == str(macos / "bin" / "python3")


def test_a_started_python_is_told_qgiss_home_and_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "packages_base", lambda: str(tmp_path / "packages"))
    plain = bootstrap.python_environment()
    assert plain["PYTHONUSERBASE"] == str(tmp_path / "packages")
    assert sys.path[1] in plain["PYTHONPATH"].split(os.pathsep)  # QGIS's packages, so pip reuses them
    assert plain.get("PYTHONHOME") == os.environ.get("PYTHONHOME")  # a real Python finds its own home
    macos = tmp_path / "QGIS.app" / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "python3").touch()
    monkeypatch.setattr(sys, "executable", str(macos / "QGIS"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    assert bootstrap.python_environment()["PYTHONHOME"] == str(tmp_path)  # the bundle's Python is not


def test_packages_go_to_the_plugins_folder_not_the_users(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "packages_base", lambda: str(tmp_path))
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert bootstrap.pip_environment()["PYTHONUSERBASE"] == str(tmp_path)
    site = bootstrap.user_site_path()
    assert site.startswith(str(tmp_path))
    assert bootstrap.add_user_site() == site
    assert site not in sys.path  # not created yet: nothing installed
    os.makedirs(site)
    bootstrap.add_user_site()
    assert sys.path[0] == site  # ahead of QGIS's own site-packages, as the user site was
