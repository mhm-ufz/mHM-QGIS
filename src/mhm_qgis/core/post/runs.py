"""The runs of a setup: the current `output/` and the archived ones (F11).

mHM overwrites `output/` on every run, so comparing runs needs an archive.
`runs/<name>/` keeps everything at the path the namelists gave it, relative to the
setup folder: the moved `output/`, any output file the namelists put elsewhere
(moved), the restart file (copied), plus `namelists/` (copies) and `run.json`.
A run's files therefore resolve the same way, current or archived (`catalog`).

A **named run** (the Execute tab's run name) writes straight into `runs/<name>/`:
`link_outputs` points the namelists' output paths there before mHM starts, and
`finish_named` copies the namelists and writes `run.json` with `linked: true`, so
its paths resolve from the setup folder (an archived run's resolve from its own).

A v5.13 multi-domain run writes every domain into one `output/` (`output/<domain>/`),
so it lists one run per domain; v6 domains write to their own folders and are
listed, archived and named domain by domain.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import multi_domain
from ..handlers.state.cache import fingerprint
from ..handlers.state.nml_settings import load_settings
from ..handlers.store.layout import project_version

RUNS = "runs"
NAME = re.compile(r"[\w.-]+")
# The namelist keys that hold output paths (v5.13 and v6), one `key(i) = "path"` per line.
_OUTPUT_KEYS = re.compile(r"^(\s*(?:dir_out|dirconfigout|output_path|output_node_path|output_poi_path|"
                          r"diagnostics_path)\s*(?:\(\s*\d+\s*\))?\s*=)(.*)$", re.IGNORECASE | re.MULTILINE)
_QUOTED = re.compile(r"""(["'])(.*?)\1""")


@dataclass(frozen=True)
class RunRef:
    root: Path          # the setup folder, or the archive folder of an archived run
    setup: Path         # the setup folder; namelist input paths are relative to it
    namelists: Path     # the folder of the namelists that ran
    domain: Path        # the folder holding the domain's inputs
    version: str = "v5.13"
    index: int = 1      # the domain's position in the namelist arrays
    name: str = ""      # the archive name; "" for the current run

    @property
    def label(self) -> str:
        return f"{self.name or 'Current (output/)'} · {self.domain.name}"


def _domains(folder):
    stored = load_settings(folder).get(multi_domain.SECTION, {}).get("domains", [])
    return multi_domain.resolve(folder, stored), stored


def _refs(setup, root, namelists, version, name, domains):
    if domains is None:
        return [RunRef(root, setup, namelists, setup, version, 1, name)]
    return [RunRef(root, setup, namelists, domain, version, index, name)
            for index, domain in enumerate(domains, start=1)]


def list_runs(folder) -> list[RunRef]:
    """The current run first, then archived runs, newest first."""
    folder = Path(folder)
    version = project_version(folder)
    domains = None
    if multi_domain.is_multi(folder):
        domains = _domains(folder)[0]
        if version == "v6":
            return [ref for domain in domains for ref in list_runs(domain)]
    written = any(path.is_file() for path in (folder / "output").rglob("*"))
    refs = _refs(folder, folder, folder, version, "", domains) if written else []
    metas = []
    for path in (folder / RUNS).glob("*/run.json"):
        try:
            metas.append((json.loads(path.read_text(encoding="utf-8")), path.parent))
        except (OSError, ValueError):
            continue
    for meta, root in sorted(metas, key=lambda item: str(item[0].get("created", "")), reverse=True):
        stored = meta.get("domains")
        refs += _refs(folder, folder if meta.get("linked") else root, root / "namelists",
                      meta.get("version", version), root.name,
                      None if stored is None else multi_domain.resolve(folder, stored))
    return refs


def unlinked(path: str) -> str:
    """An output path without a named run's `runs/<name>/` prefix."""
    return re.sub(rf"^{RUNS}/[\w.-]+/", "", path)


def _namelist_folders(folder) -> list[Path]:
    """The folders whose mhm.nml holds the output paths: each domain of a v6 combined setup."""
    folder = Path(folder)
    if multi_domain.is_multi(folder) and project_version(folder) == "v6":
        return list(_domains(folder)[0])
    return [folder]


def link_outputs(folder, name="") -> list[Path]:
    """Point the namelists' output paths into `runs/<name>/` (back to the setup for "")
    and create the folders they need; returns those folders."""
    if name and not NAME.fullmatch(name):
        raise ValueError("Name the run with letters, digits, '.', '_' or '-'.")
    prefix, made = f"{RUNS}/{name}/" if name else "", []
    for base in _namelist_folders(folder):
        path = base / "mhm.nml"
        if not path.is_file():
            if name:
                raise ValueError(f"{base.name} has no mhm.nml: save its namelists first.")
            continue  # nothing to point back to output/

        def relink(match, base=base):
            quote, text = match.groups()
            if not text or os.path.isabs(text):
                return match.group(0)
            new = prefix + unlinked(text)
            made.append(base / (new if new.endswith("/") else os.path.dirname(new) or "."))
            return f"{quote}{new}{quote}"

        old = path.read_text(encoding="utf-8")
        new = _OUTPUT_KEYS.sub(lambda match: match.group(1) + _QUOTED.sub(relink, match.group(2)), old)
        if new != old:
            path.write_text(new, encoding="utf-8")
    for created in made:
        created.mkdir(parents=True, exist_ok=True)
    return made


def finish_named(folder, name, note="") -> list[RunRef]:
    """After a named run: its namelists and `run.json` into `runs/<name>/`."""
    for base in _namelist_folders(folder):
        target = base / RUNS / name
        (target / "namelists").mkdir(parents=True, exist_ok=True)
        for path in base.glob("*.nml"):
            shutil.copy2(path, target / "namelists" / path.name)
        _write_meta(base, target, name, note, None, linked=True)
    return [ref for ref in list_runs(folder) if ref.name == name]


def _write_meta(folder, target, name, note, period, linked=False) -> None:
    from ..datamodel import current_processes

    try:
        processes = current_processes(folder)
    except ValueError:
        processes = None
    meta = {
        "name": name,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": project_version(folder),
        "period": period,
        "processes": processes,
        "fingerprint": fingerprint(sorted(p for p in target.rglob("*") if p.is_file())),
        "note": note,
        "domains": _domains(folder)[1] if multi_domain.is_multi(folder) else None,
        "linked": linked,  # paths resolve from the setup folder (a named run), not from `target`
    }
    (target / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def run_meta(ref: RunRef) -> dict:
    """`run.json` of an archived or named run; {} for the current one."""
    if not ref.name:
        return {}
    return json.loads((ref.namelists.parent / "run.json").read_text(encoding="utf-8"))


def archive(folder, name, note="", values=None) -> list[RunRef]:
    """Move the current run into `runs/<name>/` and leave an empty `output/` behind.

    The caller refuses this while mHM runs. `values` are the namelists, read when None.
    """
    from . import catalog

    folder = Path(folder)
    version = project_version(folder)
    if multi_domain.is_multi(folder) and version == "v6":
        domains = _domains(folder)[0]
        if any((domain / RUNS / name).exists() for domain in domains):
            raise ValueError(f"A run named {name} is already archived.")
        return [ref for domain in domains for ref in archive(domain, name, note)]
    if not NAME.fullmatch(name or ""):
        raise ValueError("Name the run with letters, digits, '.', '_' or '-'.")
    target = folder / RUNS / name
    if target.exists():
        raise ValueError(f"A run named {name} is already archived.")
    output = folder / "output"
    current = [ref for ref in list_runs(folder) if not ref.name]
    if not current:
        raise ValueError("There is no run in output/ to archive.")
    runs = [catalog.open_run(ref, values) for ref in current]
    try:
        period = [str(moment) for moment in runs[0].period]
    except (OSError, ValueError, KeyError):
        period = None
    target.mkdir(parents=True)
    (target / "namelists").mkdir()
    for path in folder.glob("*.nml"):
        shutil.copy2(path, target / "namelists" / path.name)
    for run in runs:
        for kind, path in run.files.items():
            if not path.is_file() or not path.is_relative_to(folder) or path.is_relative_to(output):
                continue
            destination = target / path.relative_to(folder)
            destination.parent.mkdir(parents=True, exist_ok=True)
            (shutil.copy2 if kind == "restart" else os.replace)(path, destination)
    os.replace(output, target / "output")
    output.mkdir()
    for sub in (target / "output").iterdir():
        if sub.is_dir():
            (output / sub.name).mkdir()  # v5.13 multi: mHM needs every dir_Out to exist
    _write_meta(folder, target, name, note, period)
    return [ref for ref in list_runs(folder) if ref.name == name]
