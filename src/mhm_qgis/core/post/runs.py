"""The runs of a setup: the current `output/` and the archived ones (F11).

mHM overwrites `output/` on every run, so comparing runs needs an archive.
`runs/<name>/` keeps everything at the path the namelists gave it, relative to the
setup folder: the moved `output/`, any output file the namelists put elsewhere
(moved), the restart file (copied), plus `namelists/` (copies) and `run.json`.
A run's files therefore resolve the same way, current or archived (`catalog`).

A **run case** (the Execute tab's Backup Namelist) is only a copy: mHM runs on the
setup's namelists into `output/`, and `backup_namelists` copies them into
`runs/<name>/` with their outputs pointed at `runs/<name>/output`, plus `run.json`.
A `run.json` with `linked: true` is a named run of an earlier version, whose paths
resolve from the setup folder.

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
from ..handlers.file import json as jsonio
from ..handlers.state.cache import fingerprint
from ..handlers.state.nml_settings import load_settings
from ..handlers.store.layout import project_version

RUNS = "runs"
TRANSACTIONS = ".mhm-qgis"
NAME = re.compile(r"(?!\.{1,2}$)(?!\.mhm-qgis$)[\w.-]+")
# The namelist keys that hold output paths (v5.13 and v6), one `key(i) = "path"` per line.
_OUTPUT_KEYS = re.compile(r"^(\s*(?:dir_out|dirconfigout|output_path|output_node_path|output_poi_path|"
                          r"diagnostics_path)\s*(?:\(\s*\d+\s*\))?\s*=)(.*)$", re.IGNORECASE | re.MULTILINE)
_QUOTED = re.compile(r"""(["'])(.*?)\1""")


def _valid_run_name(name) -> bool:
    return bool(NAME.fullmatch(name or "")) and name not in {".", "..", TRANSACTIONS}


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
        return f"{self.name or 'Default (output/)'} · {self.domain.name}"


def _domains(folder):
    stored = load_settings(folder).get(multi_domain.SECTION, {}).get("domains", [])
    return multi_domain.resolve(folder, stored), stored


def _refs(setup, root, namelists, version, name, domains):
    if domains is None:
        return [RunRef(root, setup, namelists, setup, version, 1, name)]
    return [RunRef(root, setup, namelists, domain, version, index, name)
            for index, domain in enumerate(domains, start=1)]


def wrote(ref: RunRef) -> bool:
    """Whether the current run wrote anything: in output/, else the mHM output file where its
    namelists put it (a setup made outside mhm-qgis may name another folder)."""
    if any(path.is_file() for path in (ref.root / "output").rglob("*")):
        return True
    from .catalog import open_run

    try:
        return open_run(ref).exists("mhm")
    except (OSError, ValueError, KeyError):  # namelists missing or unreadable: nothing to show
        return False


def list_runs(folder) -> list[RunRef]:
    """The current run first, then archived runs, newest first."""
    folder = Path(folder)
    version = project_version(folder)
    domains = None
    if multi_domain.is_multi(folder):
        domains = _domains(folder)[0]
        if version == "v6":
            return [ref for domain in domains for ref in list_runs(domain)]
    refs = _refs(folder, folder, folder, version, "", domains)
    refs = refs if wrote(refs[0]) else []
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
    return re.sub(
        rf"^{RUNS}/(?:{re.escape(TRANSACTIONS)}/pending/[\w.-]+|[\w.-]+)/",
        "",
        path,
    )


def _namelist_folders(folder) -> list[Path]:
    """The folders whose mhm.nml holds the output paths: each domain of a v6 combined setup."""
    folder = Path(folder)
    if multi_domain.is_multi(folder) and project_version(folder) == "v6":
        return list(_domains(folder)[0])
    return [folder]


def link_outputs(folder) -> list[Path]:
    """Point outputs back to `output/` (an earlier plugin version ran named runs in `runs/`)."""
    return _set_output_prefix(folder, "")


def backup_namelists(folder, name, note="") -> None:
    """Copy the namelists into `runs/<name>/`, their outputs pointed at `runs/<name>/output`.
    Nothing runs these copies."""
    if not _valid_run_name(name):
        raise ValueError("Name the run with letters, digits, '.', '_' or '-'.")
    for base in _namelist_folders(folder):
        target = base / RUNS / name
        target.mkdir(parents=True, exist_ok=True)
        for path in base.glob("*.nml"):
            shutil.copy2(path, target / path.name)
        _set_output_prefix(target, f"{RUNS}/{name}/", create=False)
        _write_meta(base, target, name, note, None)


def _set_output_prefix(folder, prefix, *, create=True) -> list[Path]:
    changes, made = [], []
    for base in _namelist_folders(folder):
        path = base / "mhm.nml"
        if not path.is_file():
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
        changes.append((path, old, new))
    if create:
        for created in made:
            created.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for path, old, new in changes:
            if new != old:
                _replace_text(path, new)
                written.append((path, old))
    except BaseException:
        for path, old in reversed(written):
            _replace_text(path, old)
        raise
    return made


def _transaction_path(base, kind, name) -> Path:
    return Path(base) / RUNS / TRANSACTIONS / kind / name


def _replace_text(path, text) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_meta(folder, target, name, note, period) -> None:
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
    }
    jsonio.write(target / "run.json", meta)


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
    if not _valid_run_name(name):
        raise ValueError("Name the run with letters, digits, '.', '_' or '-'.")
    version = project_version(folder)
    folders = _domains(folder)[0] if multi_domain.is_multi(folder) and version == "v6" else [folder]
    if any((base / RUNS / name).exists() for base in folders):
        raise ValueError(f"A run named {name} is already archived.")
    transactions = []
    try:
        for base in folders:
            transactions.append(_stage_archive(
                base,
                name,
                note,
                None if len(folders) > 1 else values,
                catalog,
            ))
        for transaction in transactions:
            os.replace(transaction["stage"], transaction["target"])
            transaction["published"] = True
        for transaction in transactions:
            _write_meta(
                transaction["folder"],
                transaction["target"],
                name,
                note,
                transaction["period"],
            )
    except BaseException:
        for transaction in reversed(transactions):
            if transaction["published"] and transaction["target"].exists():
                os.replace(transaction["target"], transaction["stage"])
                transaction["published"] = False
        for transaction in reversed(transactions):
            _rollback_archive(transaction)
        raise
    return [ref for ref in list_runs(folder) if ref.name == name]


def _stage_archive(folder, name, note, values, catalog):
    folder = Path(folder)
    target = folder / RUNS / name
    stage = _transaction_path(folder, "archive", name)
    if stage.exists():
        raise OSError(f"An unfinished archive needs recovery: {stage}")
    output = folder / "output"
    current = [ref for ref in list_runs(folder) if not ref.name]
    if not current:
        raise ValueError("There is no run in output/ to archive.")
    opened = [catalog.open_run(ref, values) for ref in current]
    try:
        period = [str(moment) for moment in opened[0].period]
    except (OSError, ValueError, KeyError):
        period = None
    transaction = {
        "folder": folder,
        "stage": stage,
        "target": target,
        "output": output,
        "period": period,
        "moved": [],
        "published": False,
    }
    try:
        (stage / "namelists").mkdir(parents=True)
        for path in folder.glob("*.nml"):
            shutil.copy2(path, stage / "namelists" / path.name)
        for run in opened:
            for kind, path in run.files.items():
                if not path.is_file() or not path.is_relative_to(folder) or path.is_relative_to(output):
                    continue
                destination = stage / path.relative_to(folder)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if kind == "restart":
                    shutil.copy2(path, destination)
                else:
                    transaction["moved"].append((destination, path))
                    os.replace(path, destination)
        destination = stage / "output"
        transaction["moved"].append((destination, output))
        os.replace(output, destination)
        output.mkdir()
        for sub in destination.iterdir():
            if sub.is_dir():
                (output / sub.name).mkdir()  # v5.13 multi: mHM needs every dir_Out to exist
        return transaction
    except BaseException:
        _rollback_archive(transaction)
        raise


def _rollback_archive(transaction) -> None:
    for destination, source in reversed(transaction["moved"]):
        if not destination.exists():
            continue
        if source.exists():
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink()
        source.parent.mkdir(parents=True, exist_ok=True)
        os.replace(destination, source)
    if transaction["stage"].exists():
        shutil.rmtree(transaction["stage"])
