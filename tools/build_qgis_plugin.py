#!/usr/bin/env python3
"""Build the QGIS repository ZIP from the shared Python package source."""
from __future__ import annotations

import argparse
import ast
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "mhm_qgis"
MAX_SIZE = 20 * 1024 * 1024
RUNTIME_SUFFIXES = {
    ".asc", ".csv", ".html", ".nc", ".nml", ".png", ".py", ".qm",
    ".toml", ".txt", ".ui", ".yaml", ".yml",
}
SKIP_NAMES = {
    "Makefile", "data_prep_mhm_workshop_2026_practise.ipynb",
    "mhmv5", "mhmv6", "pb_tool.cfg", "plugin_upload.py", "pylintrc",
}
BINARY_MAGICS = (b"\x7fELF", b"MZ", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")


def package_version() -> str:
    tree = ast.parse((PACKAGE / "_version.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and value:
                return value
    raise ValueError("_version.py must define a non-empty literal __version__.")


def runtime_files():
    for path in sorted(PACKAGE.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.name in SKIP_NAMES or "__pycache__" in path.parts:
            continue
        if path.suffix.lower() in RUNTIME_SUFFIXES:
            yield path


def _metadata(data: bytes, version: str) -> bytes:
    updated, count = re.subn(
        rb"(?m)^version=.*$", f"version={version}".encode("ascii"), data
    )
    if count != 1:
        raise ValueError("metadata.txt must contain exactly one version field.")
    return updated


def _write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    if data.startswith(BINARY_MAGICS):
        raise ValueError(f"Executable binary is forbidden in QGIS package: {name}")
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def build(output_dir: Path) -> Path:
    version = package_version()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"mhm_qgis-{version}.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for path in runtime_files():
            relative = path.relative_to(PACKAGE).as_posix()
            data = path.read_bytes()
            if relative == "metadata.txt":
                data = _metadata(data, version)
            _write(archive, f"mhm_qgis/{relative}", data)
        for name in ("LICENSE", "README.md"):
            _write(archive, f"mhm_qgis/{name}", (ROOT / name).read_bytes())
    if target.stat().st_size > MAX_SIZE:
        target.unlink()
        raise ValueError("QGIS plugin ZIP exceeds the 20 MiB repository limit.")
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "qgis")
    args = parser.parse_args(argv)
    print(build(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
