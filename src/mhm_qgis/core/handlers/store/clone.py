"""Copy mutable setup state and link reusable prepared inputs."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..file import json as jsonio
from .layout import project_version
from .paths import temp_folder, version_key


STATE_FILES = {
    "mhm_qgis_input_state.json",
    "mhm_qgis_domain_delineation_state.json",
    "mhm_qgis_processing_state.json",
    "nml-settings.json",
}
LINKED_TREES = ("data", ".temp/Morphology")


def clone_domain_inputs(source, target, version=None) -> Path:
    """Create an independent setup whose heavy prepared files are symlinks."""
    source, target = Path(source), Path(target)
    if source.is_symlink() or target.is_symlink():
        raise ValueError("Clone source and target must not be symlinks.")
    source, target = source.resolve(), target.resolve()
    if (not source.is_dir() or target.exists()
            or source == target or source in target.parents):
        raise ValueError("Clone source must exist and the new domain must not exist.")
    if version is not None and project_version(source) != version_key(version):
        raise ValueError("The source domain uses a different mHM version.")
    target.mkdir(parents=True)
    try:
        for path in source.iterdir():
            if path.name == "settings.yaml" or path.suffix == ".nml":
                shutil.copy2(path, target / path.name)
        source_state = Path(temp_folder(source))
        target_state = Path(temp_folder(target))
        target_state.mkdir(parents=True)
        for name in STATE_FILES:
            if (source_state / name).is_file():
                _copy_state(source_state / name, target_state / name, source, target)
        for name in LINKED_TREES:
            _link_tree(source / name, target / name)
    except BaseException:
        shutil.rmtree(target)
        raise
    return target


def _copy_state(source, target, old_root, new_root):
    value = jsonio.read(source)
    if value is None:
        shutil.copy2(source, target)
        return
    jsonio.write(target, _rebase(value, str(old_root), str(new_root)))


def _rebase(value, old, new):
    if isinstance(value, dict):
        return {key: _rebase(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [_rebase(item, old, new) for item in value]
    if isinstance(value, str):
        return value.replace(old, new)
    return value


def _link_tree(source, target):
    if not source.is_dir():
        return
    for folder, directories, files in os.walk(source, followlinks=False):
        folder = Path(folder)
        relative = folder.relative_to(source)
        destination = target / relative
        destination.mkdir(parents=True, exist_ok=True)
        directories[:] = [name for name in directories if not (folder / name).is_symlink()]
        for name in files:
            original = folder / name
            link = destination / name
            link.symlink_to(os.path.relpath(original.resolve(), link.parent))


__all__ = ["LINKED_TREES", "clone_domain_inputs"]
