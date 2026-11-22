"""Small, network-free checks for the private QGIS runtime bootstrap."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import mhm_qgis_runtime as runtime


def test_supported_platforms_and_user_roots(tmp_path):
    assert runtime.platform_tag("Linux", "x86_64") == "linux-64"
    assert runtime.platform_tag("Windows", "AMD64") == "win-64"
    assert runtime.platform_tag("Darwin", "arm64") == "osx-arm64"
    assert runtime.runtime_root("linux", {}, tmp_path) == (
        tmp_path / ".local/share/mhm-qgis"
    )
    assert runtime.runtime_root(
        "windows", {"LOCALAPPDATA": str(tmp_path)}, tmp_path
    ) == tmp_path / "mhm-qgis"
    with pytest.raises(runtime.RuntimeSetupError):
        runtime.platform_tag("Linux", "arm64")


@pytest.mark.skipif(os.name == "nt", reason="the POSIX installer script")
def test_pixi_from_path_or_else_its_official_installer(monkeypatch, tmp_path):
    """No pinned binary: pixi on PATH, or ~/.pixi from pixi.sh with the shell profile untouched."""
    monkeypatch.setenv("PIXI_HOME", str(tmp_path / "pixi-home"))
    monkeypatch.setattr(runtime.shutil, "which", lambda _name: "/usr/bin/pixi")
    assert runtime.ensure_pixi(opener=lambda _request: pytest.fail("no download")) == Path("/usr/bin/pixi")

    calls = []

    def install(command, **kwargs):
        calls.append((command, kwargs["input"], kwargs["env"]))
        binary = tmp_path / "pixi-home/bin/pixi"
        binary.parent.mkdir(parents=True)
        binary.touch()

    monkeypatch.setattr(runtime.shutil, "which", lambda _name: None)
    monkeypatch.setattr(runtime.subprocess, "run", install)
    assert runtime.ensure_pixi(opener=lambda _request: io.BytesIO(b"echo install")) == tmp_path / "pixi-home/bin/pixi"
    [(command, script, environment)] = calls
    assert (command, script) == (["sh"], "echo install")
    assert environment["PIXI_NO_PATH_UPDATE"] == "1"


def _install_fakes(monkeypatch, tmp_path, commands):
    pixi = tmp_path / "pixi"
    monkeypatch.setattr(runtime, "platform_tag", lambda: "linux-64")
    monkeypatch.setattr(runtime, "active_runtime", lambda *args: None)
    monkeypatch.setattr(runtime, "ensure_pixi", lambda *args: pixi)
    monkeypatch.setattr(runtime, "_smoke_test", lambda *args: "4.2.3")
    monkeypatch.setattr(runtime, "_local_wheels", lambda target: target)
    monkeypatch.setattr(runtime, "_run", lambda command, **kwargs: commands.append(list(command)))
    return pixi


def test_install_records_exact_commands(monkeypatch, tmp_path):
    commands = []
    pixi = _install_fakes(monkeypatch, tmp_path, commands)
    monkeypatch.setattr(runtime, "_confirm", lambda *args: None)
    legacy = tmp_path / "mamba/pkgs"  # the micromamba runtime's layout goes
    legacy.mkdir(parents=True)

    marker = runtime.install_runtime(yes=True, root=tmp_path)
    assert not legacy.parent.exists()
    assert marker["prefix"] == str(tmp_path / ".pixi/envs/default")
    manifest = tmp_path / "pixi.toml"
    assert manifest.read_bytes() == runtime._manifest()
    assert commands[0] == [pixi, "install", "--manifest-path", manifest]
    assert commands[1][:5] == [pixi, "run", "--frozen", "--manifest-path", manifest]
    # the user's own mhm-qgis, repacked under the runtime root, not looked up on PyPI
    assert commands[1][-3] == "--find-links"
    assert commands[1][-2].parent == tmp_path
    assert commands[1][-1] == f"mhm-qgis[full]=={runtime.__version__}"
    assert marker["qgis_version"] == "4.2.3"
    assert json.loads((tmp_path / runtime.MARKER).read_text()) == marker


def test_an_unchanged_manifest_only_reinstalls_mhm_qgis(monkeypatch, tmp_path):
    """An upgrade with the same pixi.toml keeps the environment: no solve, no download."""
    commands = []
    _install_fakes(monkeypatch, tmp_path, commands)
    monkeypatch.setattr(runtime, "_confirm", lambda *args: pytest.fail("no download to confirm"))
    prefix = tmp_path / ".pixi/envs/default"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python").touch()
    (tmp_path / runtime.MARKER).write_text(json.dumps(
        {"app_version": "0.0.0", "manifest_sha256": runtime._manifest_digest(), "prefix": str(prefix)}))

    marker = runtime.install_runtime(yes=True, root=tmp_path)
    [command] = commands
    assert command[1] == "run"
    assert "pip" in command
    assert marker["app_version"] == runtime.__version__


def test_relaunch_runs_through_pixi(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("PYTHONPATH", "/host/src")
    monkeypatch.setattr(runtime, "ensure_pixi", lambda *args: tmp_path / "pixi")
    monkeypatch.setattr(runtime.subprocess, "call", lambda command, env: calls.append((command, env)) or 7)

    assert runtime.relaunch(["--help"], tmp_path) == 7
    command, environment = calls[0]
    assert command[:5] == [str(tmp_path / "pixi"), "run", "--frozen", "--manifest-path", str(tmp_path / "pixi.toml")]
    assert command[-4:] == ["python", "-m", "mhm_qgis_runtime.app", "--help"]
    assert environment[runtime.IN_RUNTIME] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONPATH" not in environment


def test_remove_refuses_unrelated_directory(tmp_path):
    with pytest.raises(runtime.RuntimeSetupError, match="unsafe"):
        runtime.remove_runtime(tmp_path / "unrelated")
    with pytest.raises(runtime.RuntimeSetupError, match="outside"):
        runtime._safe_remove(tmp_path, tmp_path / "mhm-qgis")



def _installed(site, name, version, files, purelib=True, requires=()):
    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        + "".join(f"Requires-Dist: {requirement}\n" for requirement in requires))
    (info / "WHEEL").write_text(f"Wheel-Version: 1.0\nRoot-Is-Purelib: {str(purelib).lower()}\nTag: py3-none-any\n")
    (info / "INSTALLER").write_text("pip\n")
    for file in files:
        (site / file).parent.mkdir(parents=True, exist_ok=True)
        (site / file).write_text("x = 1\n")
    listed = [*files, "../../bin/mhm-qgis", f"{info.name}/METADATA", f"{info.name}/WHEEL",
              f"{info.name}/INSTALLER", f"{info.name}/RECORD"]
    (info / "RECORD").write_text("".join(f"{file},,\n" for file in listed))


def test_runtime_repacks_the_installed_pure_packages_for_pip(monkeypatch, tmp_path):
    """The runtime takes mhm-qgis and its pure-Python packages from this install
    (TestPyPI, a local wheel, unreleased ones); compiled ones come from pixi.toml or PyPI."""
    site = tmp_path / "site"
    _installed(site, "mhm-qgis", "9.9", ["mhm_qgis_fake/__init__.py", "mhm_qgis_fake/__pycache__/a.pyc"],
               requires=["pure-dep>=1", "compiled-dep", 'absent-dep[x]>=1; extra == "full"'])
    _installed(site, "pure-dep", "1.0", ["pure_dep/__init__.py"])
    _installed(site, "compiled-dep", "2.0", ["compiled_dep/__init__.py"], purelib=False)
    monkeypatch.syspath_prepend(str(site))

    target = tmp_path / "wheels"
    target.mkdir()
    wheels = sorted(path.name for path in runtime._local_wheels(target).iterdir())
    assert wheels == ["mhm_qgis-9.9-py3-none-any.whl", "pure_dep-1.0-py3-none-any.whl"]
    with zipfile.ZipFile(target / wheels[0]) as archive:
        assert "mhm_qgis-9.9.dist-info/INSTALLER" not in archive.namelist()
        assert not any("__pycache__" in name or name.startswith("..") for name in archive.namelist())

    pytest.importorskip("pip")
    installed = tmp_path / "installed"
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--no-index", "--quiet",
                    "--target", str(installed), *(str(target / wheel) for wheel in wheels)], check=True)
    assert (installed / "mhm_qgis_fake/__init__.py").is_file()
    assert (installed / "pure_dep/__init__.py").is_file()


def test_launch_with_qgis_but_without_the_app_packages_says_how(monkeypatch, capsys):
    monkeypatch.setattr(runtime, "_qgis_available", lambda: True)
    monkeypatch.setattr(runtime, "_missing_packages", lambda: ["mhm-tools"])
    assert runtime.launch([]) == 1
    assert "pip install 'mhm-qgis[full]'" in capsys.readouterr().err
