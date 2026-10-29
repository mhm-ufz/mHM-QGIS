"""The plugin's dependency check compares versions and markers, not just imports."""
from __future__ import annotations

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
    monkeypatch.setattr(bootstrap, "pip_supports_break_system_packages", lambda: False)
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *args, **kwargs: ran.append(args))
    for name in ("create_progress_dialog", "show_failure", "show_info", "set_wait_cursor"):
        monkeypatch.setattr(bootstrap, name, lambda *_args, **_kwargs: None)
    result = bootstrap.install_missing_dependencies(None, [bootstrap.dependency_from_requirement_line("numpy>=999")])
    assert not ran
    assert "newer QGIS" in result.failed["numpy>=999"]
