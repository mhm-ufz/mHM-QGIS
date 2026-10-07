"""Combine prepared single-domain setups into one multi-domain mHM run (Qt-free).

Domains are read, never written. v6 lists them in `combine.nml` (`config_domain`);
mHM then reads each domain's own `mhm.nml`, but takes `config_processes`,
`parameters.nml` and `outputs.nml` from the multi folder. v5.13 has one `mhm.nml`
whose per-domain arrays are filled from each domain's saved namelist, with input
paths rebased to point into the domain and outputs kept in the multi folder.
"""
from __future__ import annotations

import os
import shutil
import tomllib
from functools import cache
from pathlib import Path

import yaml

from .handlers.file import json as jsonio
from .handlers.store import layout
from .handlers.store.paths import (
    MULTI_FOLDER_NAME,
    domain_folder,
    schemas_folder,
    version_key,
)

SECTION = "multi_domain"
SHARED_FILES = {"v6": ("parameters.nml", "outputs.nml"),
                "v5.13": ("mhm_parameter.nml", "mhm_outputs.nml")}
# v5.13 per-domain outputs stay in the multi folder; only inputs point into domains.
_OUTPUT_PATHS = {"dir_Out": "output/{name}/", "mhm_file_RestartOut": "restart/{name}/mhm_restart_out.nc",
                 "mrm_file_RestartOut": "restart/{name}/mrm_restart_out.nc", "dirConfigOut": "output/"}
_NOT_REBASED = {"LCoverfName"}  # file names inside dir_LCover(i), not paths from the run folder


def multi_folder(project_directory, name) -> Path:
    return domain_folder(project_directory, name, MULTI_FOLDER_NAME)


def multi_folders(project_directory) -> tuple[Path, ...]:
    return layout.domain_folders(project_directory, MULTI_FOLDER_NAME)


def is_multi(folder) -> bool:
    return jsonio.read_mapping(layout.input_state_path(folder)).get("setup") == "multi"


def store_paths(root, paths) -> list[str]:
    """Domain paths as saved: relative to the multi folder, absolute across drives."""
    stored = []
    for path in paths:
        try:
            stored.append(Path(os.path.relpath(Path(path).resolve(), Path(root).resolve())).as_posix())
        except ValueError:
            stored.append(str(Path(path).resolve()))
    return stored


def resolve(root, stored) -> list[Path]:
    return [(Path(root) / path).resolve() for path in stored]


def _read(folder, version, lower=False) -> dict:
    from ..externals.nml_tools_handler import read_namelists

    return read_namelists(folder, schemas_folder(version), lower=lower)


@cache
def _registry(version) -> tuple[dict, dict]:
    """(group -> schema properties, profile -> its namelist names) from nml-config.toml."""
    root = schemas_folder(version)
    config = tomllib.loads((root / "nml-config.toml").read_text(encoding="utf-8"))
    properties = {
        item["name"]: yaml.safe_load((root / item["schema"]).read_text(encoding="utf-8")).get("properties", {})
        for item in config.get("namelists", [])
    }
    profiles = {item["name"]: item.get("namelists", []) for item in config.get("file_profiles", [])}
    return properties, profiles


def validate_domain(path, version, root, existing=()) -> str | None:  # noqa: PLR0911
    """Why `path` cannot join this multi setup, or None."""
    path = Path(path).resolve()
    if path == Path(root).resolve():
        return "That is this multi-domain setup itself."
    if not Path(layout.input_state_path(path)).is_file():
        return f"{path} is not a domain setup (it has no .temp/mhm_qgis_input_state.json)."
    if is_multi(path):
        return f"{path.name} is a multi-domain setup; add its domains instead."
    if any(Path(other).resolve() == path for other in existing):
        return f"{path.name} is already in the list."
    domain_version = layout.project_version(path)
    if domain_version != version_key(version):
        return f"{path.name} uses mHM {domain_version}; this setup uses {version_key(version)}."
    if not (path / "mhm.nml").is_file():
        return f"{path.name} has no saved mhm.nml: open it and save its namelists first."
    return None


def _geology_definition(path) -> str | None:
    file = Path(layout.morph_folder(path)) / "geology_classdefinition.txt"
    return file.read_text(encoding="utf-8").strip() if file.is_file() else None


def validate_set(paths, version, root) -> tuple[list[str], list[str]]:
    """Errors that block the combined run, and warnings that do not."""
    errors, warnings = [], []
    if len(paths) < 2:
        errors.append("Combine at least two domains.")
    for path in paths:
        reason = validate_domain(path, version, root)
        if reason:
            errors.append(reason)
    if errors:
        return errors, warnings
    try:
        values = [_read(path, version) for path in paths]
    except ValueError as error:
        return [str(error)], warnings
    names = [Path(path).name for path in paths]

    def compare(label, get, into):
        found = [get(value) for value in values]
        if any(item != found[0] for item in found[1:]):
            into.append(f"Different {label}: " + "; ".join(f"{n}: {f}" for n, f in zip(names, found, strict=True)))

    geology = [_geology_definition(path) for path in paths]
    if None not in geology and len(set(geology)) > 1:
        errors.append("Different geology class definitions (geology_classdefinition.txt); "
                      "the shared parameter file needs one classification.")
    if version_key(version) == "v6":
        compare("process cases (config_processes)", lambda v: v.get("config_processes"), errors)
        compare("time periods (config_time)", lambda v: v.get("config_time"), warnings)
        for name, value in zip(names, values, strict=True):
            project = value.get("config_project", {})
            if project.get("n_domains", 1) != 1 or project.get("read_domains_from_dirs"):
                errors.append(f"{name}: its mhm.nml must describe one domain "
                              "(n_domains = 1, read_domains_from_dirs = .false.).")
        warnings.append("mHM 6 writes each domain's outputs to its own output_path, inside the "
                        "domain folder, replacing that domain's single-run outputs.")
        return errors, warnings
    compare("process cases (processCase)", lambda v: v.get("processSelection", {}).get("processCase"), errors)
    compare("land-cover scenes", lambda v: {k: v.get("LCover", {}).get(k) for k in (
        "nLCoverScene", "LCoverYearStart", "LCoverYearEnd", "LCoverfName")}, errors)
    compare("soil horizons", lambda v: {k: v.get("soildata", {}).get(k) for k in (
        "nSoilHorizons_mHM", "soil_Depth", "iFlag_soilDB")}, errors)
    compare("time periods", lambda v: v.get("time_periods"), warnings)
    try:
        v5_values(paths, root, values)
    except ValueError as error:
        errors.append(str(error))
    return errors, warnings


def v6_values(paths, root, values=None) -> tuple[dict, dict]:
    """The `meta` profile (combine.nml) and the dimensions for a v6 combined run."""
    values = values or [_read(path, "6") for path in paths]
    first, count = values[0], len(paths)
    projects = [value.get("config_project", {}) for value in values]
    project = {**projects[0], "n_domains": count, "read_domains_from_dirs": True}
    dimensions = {"n_domains": count}
    for key in ("n_geo_units", "max_layers"):
        sizes = [p[key] for p in projects if p.get(key)]
        if sizes:  # each domain's value must be <= the combined one
            project[key] = dimensions[key] = max(sizes)
    meta = {"config_project": project,
            "config_domain": {"domain_dirs": store_paths(root, paths), "domain_nmls": ["mhm.nml"] * count}}
    for group in ("config_processes", "config_optimize"):
        if group in first:
            meta[group] = first[group]
    return {"meta": meta}, dimensions


def _per_domain(spec) -> bool:
    shape = spec.get("x-fortran-shape")
    return (shape[0] if isinstance(shape, list) and shape else shape) == "nDomains"


def _rebase(value, domain, root):
    if isinstance(value, list):
        return [_rebase(item, domain, root) for item in value]
    if not isinstance(value, str) or not value or os.path.isabs(value):
        return value
    target = os.path.normpath(os.path.join(domain, value))
    try:
        rebased = Path(os.path.relpath(target, root)).as_posix()
    except ValueError:
        rebased = Path(target).as_posix()
    return rebased + "/" if value.endswith("/") else rebased


def _rectangular(rows):
    """Pad per-domain rows (gauges) to the widest, as the single-domain builder does."""
    if not all(isinstance(row, list) for row in rows):
        return rows
    widest = max(len(row) for row in rows)
    sample = next((row[0] for row in rows if row), 0)
    return [row + [type(sample)()] * (widest - len(row)) for row in rows]


def _is_path(spec) -> bool:
    return (spec.get("format") or spec.get("items", {}).get("format")) in ("directory-path", "file-path")


def v5_values(paths, root, values=None) -> tuple[dict, dict]:
    """One `main` profile whose per-domain arrays come from each domain's mhm.nml."""
    properties, profiles = _registry("5.13")
    values = values or [_read(path, "5.13") for path in paths]
    names = [Path(path).name for path in paths]
    count, main = len(paths), {}
    for group in profiles.get("main", []):
        datas = [value.get(group) or {} for value in values]
        if not any(datas):
            continue
        props, combined = properties.get(group, {}), {}
        for key in dict.fromkeys(k for data in datas for k in data):
            spec = props.get(key, {})
            rebase = _is_path(spec) and key not in _NOT_REBASED
            if key in _OUTPUT_PATHS:
                template = _OUTPUT_PATHS[key]
                combined[key] = ([template.format(name=n) for n in names] if _per_domain(spec)
                                 else template.format(name=names[0]))
            elif _per_domain(spec):
                items = []
                for name, path, data in zip(names, paths, datas, strict=True):
                    value = data.get(key)
                    if not isinstance(value, list) or not value:
                        raise ValueError(f"{name}: {group}.{key} is not set; save its namelists again.")
                    items.append(_rebase(value[0], path, root) if rebase else value[0])
                combined[key] = _rectangular(items)
            else:
                value = datas[0].get(key)
                combined[key] = _rebase(value, paths[0], root) if rebase else value
        main[group] = combined
    main.setdefault("mainconfig", {}).update(nDomains=count, L0Domain=list(range(1, count + 1)))
    gauges = sum(main.get("evaluation_gauges", {}).get("NoGauges_domain") or [0])
    if "evaluation_gauges" in main:
        main["evaluation_gauges"]["nGaugesTotal"] = gauges
    if "NoInflowGauges_domain" in main.get("inflow_gauges", {}):
        main["inflow_gauges"]["nInflowGaugesTotal"] = sum(main["inflow_gauges"]["NoInflowGauges_domain"])
    first = values[0]
    dimensions = {
        "nDomains": count,
        # The editor sizes the gauge arrays by the widest row; the namelist value
        # nGaugesTotal is the sum (mHM sizes its discharge buffers from it).
        "nGaugesTotal": max((len(row) for row in main.get("evaluation_gauges", {}).get("Gauge_id") or []),
                            default=0) or 1,
        "nGeoUnits": len(first.get("geoparameter", {}).get("GeoParam") or []) or 1,
        "nSoilHorizons": int(first.get("soildata", {}).get("nSoilHorizons_mHM") or 1),
        "nLcoverScene": int(first.get("LCover", {}).get("nLCoverScene") or 1),
    }
    return {"main": main}, dimensions


def prepare(paths, version, root) -> list[str]:
    """Create the multi folder's output and restart folders and seed its shared namelists.

    The parameter and output namelists are copied from the first domain when the
    multi folder has none yet; afterwards they are edited in the multi folder only.
    Returns the copied file names.
    """
    layout.ensure_multi_structure(root)
    for path in paths:
        name = Path(path).name
        (Path(root) / "output" / name).mkdir(parents=True, exist_ok=True)
        (Path(root) / "restart" / name).mkdir(parents=True, exist_ok=True)
    copied = []
    for file in SHARED_FILES[version_key(version)]:
        source, target = Path(paths[0]) / file, Path(root) / file
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)
            copied.append(file)
    return copied
