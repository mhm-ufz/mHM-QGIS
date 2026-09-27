"""The lazy, Qt-free boundary to nml-tools' profile editor."""
from contextlib import contextmanager
from pathlib import Path
import os
import shutil
from tempfile import TemporaryDirectory


def launch_profile_editor(schemas_dir, output_dir, initial_values=None,
                          initial_dimensions=None, *, file_profiles=None, log=None):
    from nml_tools import gui
    from .mhm_tools_handler import capture_messages

    profiles = file_profiles or {"main": [], "parameter": [], "output": []}
    with capture_messages(log, logger_names=("nml_tools",)):
        with _editor_folder(schemas_dir, output_dir, profiles, initial_values or {},
                            initial_dimensions or {}, log) as folder:
            return gui.launch_gui(schemas_dir, folder, profiles,
                                  initial_values=initial_values,
                                  initial_dimensions=initial_dimensions)


@contextmanager
def _editor_folder(schemas, output, profiles, initial, dimensions, log=None):
    """Resize legacy namelists in scratch; commit only files the editor saves."""
    from nml_tools.gui.model import (
        load_project, load_profile, overlay_values, recover_dimensions,
        save_profiles, suggestion,
    )

    output = Path(output)
    # nml-tools keys dimensions in lower case; v5 names them `nDomains`.
    dimensions = {key.lower(): size for key, size in dimensions.items()}
    project = load_project(schemas, output, profiles)
    previous = recover_dimensions(project)
    shrunk = {key for key, size in dimensions.items() if size < previous.get(key, size)}
    if not shrunk or not any((output / p.default_file).is_file() for p in project.profiles):
        yield output
        return
    sizes = {**project.constants, **previous, **dimensions}
    with TemporaryDirectory(prefix=".namelist-edit-", dir=output) as temporary:
        folder = Path(temporary)
        for entry in output.iterdir():
            if entry == folder or entry.name.startswith(".namelist-edit-"):
                continue
            if entry.is_file() and entry.suffix == ".nml":
                shutil.copy2(entry, folder / entry.name)
            elif entry.is_dir():
                (folder / entry.name).symlink_to(entry.resolve(), target_is_directory=True)
        staged = load_project(schemas, folder, profiles)
        updates = []
        for profile in project.profiles:
            values = load_profile(project, profile, previous)
            for page in profile.pages:
                fields = values.get(page.name, {})
                for name, schema in page.schema.get("properties", {}).items():
                    shape = schema.get("x-fortran-shape", [])
                    shape = shape if isinstance(shape, list) else [shape]
                    if name in fields and shrunk.intersection(str(t).lower() for t in shape):
                        # A merged domain has no single predecessor: do not pick
                        # one old catchment's settings arbitrarily.
                        fields[name] = suggestion(schema, sizes)
            values = overlay_values(values, initial.get(profile.key, {}))
            updates.append((staged.profile(profile.name), values, dimensions))
        save_profiles(staged, updates)
        before = {p: p.stat().st_mtime_ns for p in folder.glob("*.nml")}
        if log:
            log("Legacy domain arrays are reset in the editor preview; originals are preserved until Save.")
        yield folder
        for path in folder.glob("*.nml"):
            if before.get(path) == path.stat().st_mtime_ns:
                continue
            target = output / path.name
            backup = target.with_suffix(".nml.before-single-domain")
            if target.exists() and not backup.exists():
                shutil.copy2(target, backup)
            os.replace(path, target)


__all__ = ["launch_profile_editor"]
