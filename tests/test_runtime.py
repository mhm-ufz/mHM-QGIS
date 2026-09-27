"""Small, network-free checks for the private QGIS runtime bootstrap."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from mhm_qgis import runtime


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


def test_micromamba_download_requires_expected_hash(monkeypatch, tmp_path):
    payload = b"micromamba"
    spec = {
        "url": "https://example.invalid/micromamba",
        "size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    monkeypatch.setattr(
        runtime, "_manifest", lambda: {"platforms": {"linux-64": spec}}
    )

    target = runtime.ensure_micromamba(
        tmp_path, "linux-64", opener=lambda request: io.BytesIO(payload)
    )
    assert target.read_bytes() == payload

    target.unlink()
    spec["sha256"] = "0" * 64
    with pytest.raises(runtime.RuntimeSetupError, match="checksum"):
        runtime.ensure_micromamba(
            tmp_path, "linux-64", opener=lambda request: io.BytesIO(payload)
        )


def test_install_records_exact_commands(monkeypatch, tmp_path):
    wheelhouse = tmp_path / "wheels"
    wheelhouse.mkdir()
    micromamba = tmp_path / "bin/micromamba"
    commands = []

    monkeypatch.setattr(runtime, "platform_tag", lambda: "linux-64")
    monkeypatch.setattr(runtime, "active_runtime", lambda *args: None)
    monkeypatch.setattr(runtime, "_confirm", lambda *args: None)
    monkeypatch.setattr(runtime, "ensure_micromamba", lambda *args: micromamba)
    monkeypatch.setattr(runtime, "_smoke_test", lambda *args: "3.44.14")
    monkeypatch.setattr(
        runtime, "_run", lambda command, **kwargs: commands.append(list(command))
    )
    previous = tmp_path / "envs/old"
    previous.mkdir(parents=True)
    (tmp_path / runtime.MARKER).write_text(json.dumps({"prefix": str(previous)}))

    marker = runtime.install_runtime(
        yes=True, wheelhouse=wheelhouse, root=tmp_path
    )
    assert not previous.exists()

    assert "create" in commands[0]
    assert commands[0][-2] == "--file"
    assert commands[1][-3:] == [
        "--find-links",
        wheelhouse.absolute(),
        f"mhm-qgis=={runtime.__version__}",
    ]
    assert marker["qgis_version"] == "3.44.14"
    assert json.loads((tmp_path / runtime.MARKER).read_text()) == marker


def test_relaunch_uses_private_prefix(monkeypatch, tmp_path):
    calls = []
    marker = {"platform": "linux-64", "prefix": str(tmp_path / "env")}
    monkeypatch.setenv("PYTHONPATH", "/host/src")
    monkeypatch.setattr(
        runtime.subprocess,
        "call",
        lambda command, env: calls.append((command, env)) or 7,
    )

    assert runtime.relaunch(marker, ["--help"], tmp_path) == 7
    command, environment = calls[0]
    assert command[-4:] == ["python", "-m", "mhm_qgis.qt", "--help"]
    assert environment[runtime.IN_RUNTIME] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONPATH" not in environment


def test_remove_refuses_unrelated_directory(tmp_path):
    with pytest.raises(runtime.RuntimeSetupError, match="unsafe"):
        runtime.remove_runtime(tmp_path / "unrelated")
    with pytest.raises(runtime.RuntimeSetupError, match="outside"):
        runtime._safe_remove(tmp_path, tmp_path / "mhm-qgis")

