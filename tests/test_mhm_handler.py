"""Tests for resolving external mHM executables."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from mhm_qgis.externals import mhm_handler


def _executable(path: Path) -> Path:
    path.write_text("", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_executable_uses_installed_v5_and_supplied_v6(tmp_path, monkeypatch):
    v5 = _executable(tmp_path / "mhm")
    v6 = _executable(tmp_path / "mhmv6")
    monkeypatch.setattr(mhm_handler.shutil, "which", lambda name: str(v5))
    monkeypatch.setattr(
        mhm_handler.subprocess,
        "run",
        lambda command, **options: SimpleNamespace(
            returncode=0,
            stdout="5.13.4" if Path(command[0]) == v5 else "6.0.0",
            stderr="",
        ),
    )

    assert mhm_handler.executable("5.13") == v5
    assert mhm_handler.executable("6", v6_path=v6) == v6


def test_a_broken_mhm_on_path_is_skipped_for_the_pip_installed_one(tmp_path, monkeypatch):
    """An old build on PATH (missing its libraries) must not hide pip's `mhm`."""
    for folder in ("broken", "user/bin"):
        (tmp_path / folder).mkdir(parents=True)
    broken = _executable(tmp_path / "broken" / "mhm")
    good = _executable(tmp_path / "user" / "bin" / "mhm")
    monkeypatch.setattr(mhm_handler.shutil, "which", lambda name: str(broken))
    monkeypatch.setattr(mhm_handler.sysconfig, "get_path", lambda name: str(tmp_path / "none"))
    monkeypatch.setattr(mhm_handler.site, "getuserbase", lambda: str(tmp_path / "user"))
    monkeypatch.setattr(mhm_handler.subprocess, "run", lambda command, **options: SimpleNamespace(
        returncode=0 if Path(command[0]) == good else 127,
        stdout="5.13.5" if Path(command[0]) == good else "",
        stderr="" if Path(command[0]) == good else "libnetcdf.so.19: cannot open shared object file"))
    assert mhm_handler.executable("5.13") == good  # any 5.13.x
    monkeypatch.setattr(mhm_handler.site, "getuserbase", lambda: str(tmp_path / "none"))
    with pytest.raises(FileNotFoundError, match=r"Skipped: Expected mHM 5\.13"):
        mhm_handler.executable("5.13")


def test_v6_environment_override_is_validated(tmp_path, monkeypatch):
    v6 = _executable(tmp_path / "mhmv6")
    monkeypatch.setenv(mhm_handler.V6_ENVIRONMENT_VARIABLE, str(v6))
    monkeypatch.setattr(
        mhm_handler.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="mHM 5.13.4", stderr=""
        ),
    )

    with pytest.raises(ValueError, match="Expected mHM v6"):
        mhm_handler.executable("6")


def test_run_uses_selected_v6_binary(tmp_path, monkeypatch):
    binary = _executable(tmp_path / "mhmv6")
    calls = []
    monkeypatch.setattr(
        mhm_handler,
        "executable",
        lambda version, v6_path=None: Path(v6_path),
    )

    assert mhm_handler.run(
        "6",
        tmp_path,
        lambda command, **options: calls.append((command, options)) or True,
        v6_path=binary,
    )
    assert calls == [(
        str(binary),
        {"cwd": str(tmp_path), "show": True},
    )]


def test_v5_multi_run_uses_the_real_mrm_group_only_while_running(tmp_path, monkeypatch):
    binary = _executable(tmp_path / "mhm")
    root = tmp_path / "mhm-qgis-multi" / "combined"
    root.mkdir(parents=True)
    outputs = root / "mrm_outputs.nml"
    outputs.write_text("&NLoutputResults_mrm\n  timeStep = -2\n/\n", encoding="utf-8")
    callbacks = []
    finished = []
    monkeypatch.setattr(mhm_handler, "executable", lambda *_args, **_kwargs: binary)

    def launch(_command, **options):
        assert outputs.read_text(encoding="utf-8").startswith("&NLoutputResults\n")
        callbacks.append(options["on_finished"])
        return True

    assert mhm_handler.run("5.13", root, launch, on_finished=finished.append)
    assert outputs.read_text(encoding="utf-8").startswith("&NLoutputResults\n")
    callbacks[0](0)
    assert finished == [0]
    assert outputs.read_text(encoding="utf-8").startswith("&NLoutputResults_mrm\n")
