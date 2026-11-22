"""The standalone `mhm-qgis` command: run the app with an importable QGIS, or install a
private pixi QGIS runtime and relaunch the app inside it.

Only in the PyPI wheel. The QGIS plugin (`mhm_qgis`) never imports this package."""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import importlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from importlib import metadata
from importlib.resources import files
from pathlib import Path
from urllib.request import Request, urlopen

from mhm_qgis._version import __version__
from mhm_qgis.core.handlers.file.json import read_mapping, write

APP_NAME = "mhm-qgis"
IN_RUNTIME = "MHM_QGIS_IN_RUNTIME"
MARKER = "runtime.json"
RECOVERY = "mhm-qgis-runtime remove && mhm-qgis-runtime install"


class RuntimeSetupError(RuntimeError):
    """Private QGIS runtime setup failed."""


def platform_tag(system=None, machine=None) -> str:
    """Return the supported conda platform for this computer."""
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    tags = {
        ("linux", "x86_64"): "linux-64",
        ("linux", "amd64"): "linux-64",
        ("windows", "amd64"): "win-64",
        ("windows", "x86_64"): "win-64",
        ("darwin", "x86_64"): "osx-64",
        ("darwin", "arm64"): "osx-arm64",
        ("darwin", "aarch64"): "osx-arm64",
    }
    try:
        return tags[(system, machine)]
    except KeyError as error:
        raise RuntimeSetupError(
            f"Private QGIS runtime is not available for {system}/{machine}."
        ) from error


def runtime_root(system=None, environ=None, home=None) -> Path:
    """Return the unprivileged platform user-data directory."""
    system = (system or platform.system()).lower()
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else Path(home)
    if system == "windows":
        base = Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    elif system == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / APP_NAME


def _manifest() -> bytes:
    return files(__package__).joinpath("pixi.toml").read_bytes()


def _manifest_digest() -> str:
    return hashlib.sha256(_manifest()).hexdigest()


def _prefix(root: Path) -> Path:
    """pixi's environment, reused while pixi.toml is unchanged (an upgrade only reinstalls mhm-qgis)."""
    return root / ".pixi" / "envs" / "default"


def _python(prefix: Path, tag: str) -> Path:
    return prefix / ("python.exe" if tag == "win-64" else "bin/python")


def find_pixi() -> Path | None:
    """pixi on PATH, or where its installer puts it (`$PIXI_HOME`, by default ~/.pixi)."""
    home = Path(os.environ.get("PIXI_HOME") or Path.home() / ".pixi")
    installed = home / "bin" / ("pixi.exe" if os.name == "nt" else "pixi")
    found = shutil.which("pixi") or (installed if installed.is_file() else None)
    return Path(found) if found else None


def ensure_pixi(opener=urlopen) -> Path:
    """Return pixi, installing it with the official installer when missing; shell profiles stay untouched."""
    if found := find_pixi():
        return found
    environment = {**os.environ, "PIXI_NO_PATH_UPDATE": "1"}
    if os.name == "nt":
        command, script = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                           "irm -useb https://pixi.sh/install.ps1 | iex"], None
    else:  # the script itself, so neither curl nor wget is needed
        request = Request("https://pixi.sh/install.sh", headers={"User-Agent": f"{APP_NAME}/{__version__}"})
        with opener(request) as response:
            script = response.read().decode()
        command = ["sh"]
    try:
        subprocess.run(command, input=script, text=True, check=True, env=environment)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeSetupError(f"Installing pixi failed ({error}). Install it from https://pixi.sh and retry.") \
            from error
    if found := find_pixi():
        return found
    raise RuntimeSetupError("pixi was installed but is not in ~/.pixi/bin. Install it from https://pixi.sh and retry.")


def _pixi_run(root: Path) -> list:
    """Run in the runtime with its activation, from the lock its install wrote: no solve, no network."""
    return [ensure_pixi(), "run", "--frozen", "--manifest-path", root / "pixi.toml"]


def _marker(root: Path) -> dict:
    return read_mapping(root / MARKER)


def active_runtime(root=None, tag=None) -> dict | None:
    """Return the current matching runtime marker, or ``None``."""
    root = Path(root) if root is not None else runtime_root()
    tag = tag or platform_tag()
    marker = _marker(root)
    prefix = _prefix(root)
    expected = {
        "app_version": __version__,
        "platform": tag,
        "manifest_sha256": _manifest_digest(),
        "prefix": str(prefix),
    }
    if not all(marker.get(key) == value for key, value in expected.items()):
        return None
    return marker if _python(prefix, tag).is_file() and find_pixi() else None


def _environment() -> dict:
    environment = os.environ.copy()
    environment["PYTHONNOUSERSITE"] = "1"
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    return environment


def _run(command, *, capture=False):
    try:
        return subprocess.run(
            [str(part) for part in command],
            check=True,
            text=True,
            capture_output=capture,
            env=_environment(),
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "").strip()
        raise RuntimeSetupError(
            detail or f"Command failed: {' '.join(map(str, command))}"
        ) from error


_SMOKE_TEST = r"""
import json
from qgis.core import Qgis, QgsApplication
app = QgsApplication([], False)
app.initQgis()
try:
    from mhm_qgis.qgis_bridge.processing import initialize
    initialize()
    providers = {provider.id() for provider in QgsApplication.processingRegistry().providers()}
    missing = {"native", "gdal"} - providers
    if missing:
        raise RuntimeError("Missing QGIS Processing providers: " + ", ".join(sorted(missing)))
    print(json.dumps({"qgis_version": Qgis.QGIS_VERSION}))
finally:
    app.exitQgis()
"""


def _smoke_test(root: Path) -> str:
    command = [*_pixi_run(root), "python", "-c", _SMOKE_TEST]
    environment = _environment()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    try:
        result = subprocess.run(
            [str(part) for part in command],
            check=True,
            text=True,
            capture_output=True,
            env=environment,
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        return str(payload["qgis_version"])
    except (subprocess.CalledProcessError, IndexError, KeyError, ValueError) as error:
        detail = getattr(error, "stderr", "") or getattr(error, "stdout", "")
        raise RuntimeSetupError(
            f"Private QGIS runtime smoke test failed in {_prefix(root)}. {detail}\n"
            "Retry with: mhm-qgis-runtime install"
        ) from error


def _confirm(root: Path, yes: bool) -> None:
    print(
        f"mHM-QGIS {__version__} will install a private QGIS 4.2 runtime with pixi.\n"
        f"Location: {root}\n"
        f"pixi: {find_pixi() or 'installed into ~/.pixi from https://pixi.sh'}\n"
        "QGIS environment: approximately 1 GiB download and several GiB installed.\n"
        "Sources: conda-forge and PyPI. Licenses include QGIS GPL-2.0 and each "
        "dependency's own license.",
        flush=True,  # before pixi's own output, also in a log file
    )
    if yes:
        return
    if not sys.stdin.isatty():
        raise RuntimeSetupError(
            "Installation needs confirmation. Run: mhm-qgis-runtime install --yes"
        )
    if input("Continue? [y/N] ").strip().lower() not in {"y", "yes"}:
        raise RuntimeSetupError("Private QGIS runtime installation cancelled.")


def _safe_remove(path: Path, root: Path) -> None:
    path = path.absolute()
    root = root.absolute()
    if path != root and root not in path.parents:
        raise RuntimeSetupError(f"Refusing to remove path outside runtime root: {path}")
    if path.exists():
        shutil.rmtree(path)


def _repack(dist, target: Path) -> Path:
    """Write an installed pure-Python distribution back into a wheel."""
    skipped = {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}
    files = [file for file in dist.files
             if file.parts[0] != ".." and "__pycache__" not in file.parts and file.name not in skipped]
    info = next(file.parent for file in dist.files if file.name == "METADATA").as_posix()
    wheel = target / f"{re.sub(r'[-_.]+', '_', dist.metadata['Name'])}-{dist.version}-py3-none-any.whl"
    record = []
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            data = dist.locate_file(file).read_bytes()
            archive.writestr(file.as_posix(), data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            record.append(f"{file.as_posix()},sha256={digest},{len(data)}")
        archive.writestr(f"{info}/RECORD", "\n".join([*record, f"{info}/RECORD,,"]) + "\n")
    return wheel


def _local_wheels(target: Path) -> Path:
    """Repack the installed mhm-qgis and the pure-Python packages it requires, so the
    runtime gets exactly these, wherever they came from (TestPyPI, a local wheel, an
    editable checkout, an unreleased dependency). Compiled ones come from pixi.toml or PyPI."""
    seen, names = set(), [APP_NAME]
    while names:
        key = re.sub(r"[-_.]+", "-", names.pop()).lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            dist = metadata.distribution(key)
        except metadata.PackageNotFoundError:
            continue  # an extra or a marker that does not apply here
        names += [re.match(r"[A-Za-z0-9._-]+", line).group() for line in dist.requires or ()]
        if dist.files and "Root-Is-Purelib: true" in (dist.read_text("WHEEL") or ""):
            with contextlib.suppress(OSError, StopIteration):  # a damaged install: from the index
                _repack(dist, target)
    return target


def install_runtime(*, yes=False, root=None) -> dict:
    """Install and verify the current private runtime."""
    root = Path(root) if root is not None else runtime_root()
    tag = platform_tag()
    current = active_runtime(root, tag)
    if current:
        return current

    digest = _manifest_digest()
    prefix = _prefix(root)
    previous = _marker(root)
    # Same pixi.toml, environment in place: only mhm-qgis changes, no solve or download.
    reuse = (previous.get("manifest_sha256") == digest and previous.get("prefix") == str(prefix)
             and _python(prefix, tag).is_file())
    if not reuse:
        _confirm(root, yes)
    pixi = ensure_pixi()
    if not reuse:
        root.mkdir(parents=True, exist_ok=True)
        (root / "pixi.toml").write_bytes(_manifest())
        try:
            _run([pixi, "install", "--manifest-path", root / "pixi.toml"])
        except RuntimeSetupError as error:
            raise RuntimeSetupError(f"{error}\nAn older pixi may not read this manifest: pixi self-update") \
                from error
    # under the runtime root: /tmp can be small (an HPC)
    with tempfile.TemporaryDirectory(dir=root) as wheels:
        _run([
            *_pixi_run(root), "python", "-m", "pip", "install",
            "--find-links", _local_wheels(Path(wheels)), f"{APP_NAME}[full]=={__version__}",
        ])
    marker = {
        "app_version": __version__,
        "platform": tag,
        "manifest_sha256": digest,
        "prefix": str(prefix),
        "qgis_version": _smoke_test(root),
    }
    write(root / MARKER, marker, newline=True, fsync=True)
    for legacy in ("envs", "mamba", "bin"):  # the micromamba runtime's layout
        with contextlib.suppress(OSError, RuntimeSetupError):
            _safe_remove(root / legacy, root)
    return marker


def remove_runtime(root=None) -> bool:
    """Remove only the private mHM-QGIS runtime directory."""
    root = Path(root) if root is not None else runtime_root()
    if root.name != APP_NAME or root.parent == root:
        raise RuntimeSetupError(f"Refusing unsafe runtime path: {root}")
    existed = root.exists()
    _safe_remove(root, root)
    return existed


def relaunch(argv=None, root=None) -> int:
    """Run the existing Qt application inside the private environment."""
    root = Path(root) if root is not None else runtime_root()
    command = [*_pixi_run(root), "python", "-m", "mhm_qgis_runtime.app",
               *(sys.argv[1:] if argv is None else argv)]
    environment = _environment()
    environment[IN_RUNTIME] = "1"
    try:
        return subprocess.call([str(part) for part in command], env=environment)
    except KeyboardInterrupt:
        return 130


def _qgis_available() -> bool:
    try:
        importlib.import_module("qgis")
        return True
    except ImportError:
        return False


def _missing_packages() -> list[str]:
    from mhm_qgis.dependency_bootstrap import can_import, read_qgis_requirements

    return [dependency.package_name for dependency in read_qgis_requirements() if not can_import(dependency)]


def launch(argv=None) -> int:
    """Console entry point: use current QGIS or provision/relaunch privately."""
    try:
        if _qgis_available():
            missing = _missing_packages()
            if missing:
                raise RuntimeSetupError(
                    f"QGIS is in this environment, but the app's packages are not ({', '.join(missing)}). "
                    "Install them with: pip install 'mhm-qgis[full]'"
                )
            from .app import main as app_main

            return app_main(argv)
        if os.environ.get(IN_RUNTIME):
            raise RuntimeSetupError(
                f"QGIS could not import inside the private runtime {sys.prefix}. "
                f"Repair it with: {RECOVERY}"
            )
        root = runtime_root()
        if not active_runtime(root):
            install_runtime(root=root)
        return relaunch(argv, root)
    except (OSError, RuntimeSetupError) as error:
        print(f"mhm-qgis: {error}", file=sys.stderr)
        return 1


def main(argv=None) -> int:
    """Manage the private QGIS runtime."""
    parser = argparse.ArgumentParser(prog="mhm-qgis-runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install")
    install.add_argument("--yes", action="store_true")
    commands.add_parser("status")
    commands.add_parser("remove")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            marker = install_runtime(yes=args.yes)
            print(f"Private QGIS {marker['qgis_version']} runtime ready: {marker['prefix']}")
            return 0
        if args.command == "status":
            marker = active_runtime()
            if marker:
                print(f"Private QGIS {marker['qgis_version']} runtime: {marker['prefix']}")
                return 0
            print("Private QGIS runtime is not installed.")
            return 1
        removed = remove_runtime()
        print("Private QGIS runtime removed." if removed else "Private QGIS runtime is not installed.")
        return 0
    except (OSError, RuntimeSetupError) as error:
        print(f"mhm-qgis-runtime: {error}", file=sys.stderr)
        return 1


__all__ = [
    "active_runtime",
    "ensure_pixi",
    "find_pixi",
    "install_runtime",
    "launch",
    "main",
    "platform_tag",
    "relaunch",
    "remove_runtime",
    "runtime_root",
]
