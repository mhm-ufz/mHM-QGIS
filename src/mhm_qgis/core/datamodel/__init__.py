"""What an mHM run needs (`rules.yml`) and what it writes (`outputs.yml`).

Qt-free. The rules are verified against the mHM source of each version; the
`sources` entry of each file names where.
"""
from __future__ import annotations

from collections import namedtuple
from functools import cache
from pathlib import Path

import yaml

from ..handlers.store import layout, registry
from ..handlers.store.paths import data_folder, schemas_folder, version_key

Finding = namedtuple("Finding", "input label ok reasons message")

_HERE = Path(__file__).parent
_RULE_KEYS = {"version", "inputs", "always", "processes", "sources", "overrides"}
_CASE_KEYS = {"inputs", "needs", "label"}
_OUTPUT_KEYS = {"version", "outputs", "analyses", "observables", "files", "switches"}
_STEP_DAYS = {-1: 1, -2: 28, -3: 365}  # output step codes: daily, monthly, yearly


@cache
def _yaml(name, keys):
    data = yaml.safe_load((_HERE / name).read_text(encoding="utf-8"))
    unknown = set(data) - set(keys)
    if unknown:
        raise ValueError(f"{name}: unknown sections {sorted(unknown)}")
    return data


@cache
def load_rules(version) -> dict:
    """Return the input rules for a version: the base merged with its overrides."""
    rules = _yaml("rules.yml", frozenset(_RULE_KEYS))
    override = (rules.get("overrides") or {}).get(version_key(version)) or {}
    merged = {
        "inputs": {**rules["inputs"], **override.get("inputs", {})},
        "always": override.get("always", rules["always"]),
        "processes": {**rules["processes"], **override.get("processes", {})},
    }
    for name, process in merged["processes"].items():
        for case, spec in (process.get("cases") or {}).items():
            unknown = set(spec or {}) - _CASE_KEYS
            if unknown:
                raise ValueError(f"rules.yml: {name} case {case}: unknown keys {sorted(unknown)}")
            for item in (spec or {}).get("inputs", []):
                ids = item["any_of"] if isinstance(item, dict) else [item]
                missing = [i for i in ids if i not in merged["inputs"]]
                if missing:
                    raise ValueError(f"rules.yml: {name} case {case}: unknown inputs {missing}")
    return merged


def _case(rules, process, case):
    cases = rules["processes"].get(process, {}).get("cases") or {}
    return cases.get(int(case)) if case is not None else None


def _name(process) -> str:
    return "PET" if process == "pet" else process.replace("_", " ")


def _label(rules, process, case):
    spec = _case(rules, process, case) or {}
    suffix = f" ({spec['label']})" if spec.get("label") else ""
    return f"{_name(process)} case {case}{suffix}"


def case_label(version, process, case) -> str:
    """e.g. 'pet case 1 (Hargreaves-Samani)'."""
    return _label(load_rules(version), process, case)


def _key(item) -> str:
    return "|".join(item["any_of"]) if isinstance(item, dict) else item


def required_inputs(version, processes) -> dict[str, list[str]]:
    """Map each needed input (or `any_of` group, joined by '|') to its reasons."""
    rules = load_rules(version)
    needed = {_key(item): ["every run"] for item in rules["always"]}
    for process, case in (processes or {}).items():
        spec = _case(rules, process, case)
        if spec is None:
            continue
        reason = _label(rules, process, case)
        for item in spec.get("inputs", []):
            needed.setdefault(_key(item), []).append(reason)
    return needed


def unmet_needs(version, processes) -> list[str]:
    """Messages for unknown cases, and active cases whose required processes are off."""
    rules = load_rules(version)
    messages = []
    for process, case in (processes or {}).items():
        known = rules["processes"].get(process)
        if known is not None and _case(rules, process, case) is None:
            valid = ", ".join(map(str, known.get("cases") or {}))
            messages.append(f"{_name(process)} case {case} is not available in mHM "
                            f"{version_key(version).removeprefix('v')} (valid: {valid}).")
        for other, cases in ((_case(rules, process, case) or {}).get("needs") or {}).items():
            if int((processes or {}).get(other, 0)) not in cases:
                messages.append(f"{_label(rules, process, case)} needs {_name(other)} case {' or '.join(map(str, cases))}.")
    return messages


def meteo_variables(version, processes) -> list[str]:
    """The forcing variables the processes read, in rule order."""
    rules = load_rules(version)
    names = []
    for key in required_inputs(version, processes):
        for name in key.split("|"):
            meteo = rules["inputs"][name].get("locate", {}).get("meteo")
            if meteo and meteo not in names:
                names.append(meteo)
    return names


def feasible_cases(version, available) -> dict[str, list[int]]:
    """Per process, the cases whose inputs are all among `available` (plus `always`)."""
    rules = load_rules(version)
    have = set(available) | {name for item in rules["always"] for name in _key(item).split("|")}
    result = {}
    for process, spec in rules["processes"].items():
        result[process] = [
            int(case) for case, case_spec in (spec.get("cases") or {}).items()
            if all(
                (set(item["any_of"]) & have) if isinstance(item, dict) else item in have
                for item in (case_spec or {}).get("inputs", [])
            )
        ]
    return result


def read_namelists(project_folder) -> dict:
    """The setup's saved namelists, read through its version's schemas."""
    from ...externals.nml_tools_handler import read_namelists as read

    return read(project_folder, schemas_folder(layout.project_version(project_folder)))


def current_processes(project_folder) -> dict[str, int] | None:
    """Process cases from the saved main namelist, or None before it is saved."""
    values = read_namelists(project_folder)
    if "config_processes" in values:
        return {name: int(case) for name, case in values["config_processes"].items()}
    cases = values.get("processselection", {}).get("processcase")
    if not cases:
        return None
    rules = load_rules("5.13")
    by_index = {spec["v5_13_index"]: name for name, spec in rules["processes"].items()
                if "v5_13_index" in spec}
    return {by_index[index]: int(case) for index, case in enumerate(cases, start=1)
            if index in by_index}


def _netcdf_variables(path):
    if not path.is_file():
        return set()
    import netCDF4

    with netCDF4.Dataset(path) as dataset:
        return set(dataset.variables)


def _present(project_folder, spec) -> bool:
    locate = spec.get("locate", {})
    if "meteo" in locate:
        paths = layout.expected_meteo_outputs(project_folder).get(locate["meteo"])
        return bool(paths) and paths["netcdf"].is_file()
    if "morph" in locate:
        names = locate["morph"] if isinstance(locate["morph"], list) else [locate["morph"]]
        folder = Path(layout.morph_folder(project_folder))
        if layout.is_v6(project_folder):
            bundled = _netcdf_variables(folder / "input.nc")
            return any(name in bundled or (folder / f"{name}.nc").is_file() for name in names)
        return any(registry.first_existing(registry.variants(folder / f"{name}.asc")) for name in names)
    if "lai" in locate:
        return (Path(layout.lai_folder(project_folder)) / f"{locate['lai']}.nc").is_file()
    if locate.get("latlon"):
        return (Path(data_folder(project_folder)) / "latlon.nc").is_file()
    return False


def check(project_folder, processes=None) -> list[Finding]:
    """One finding per needed input; `ok` is False for what is missing, and why."""
    version = layout.project_version(project_folder)
    rules = load_rules(version)
    if processes is None:
        processes = current_processes(project_folder) or {}
    findings = []
    for key, reasons in required_inputs(version, processes).items():
        names = key.split("|")
        specs = [rules["inputs"][name] for name in names]
        ok = any(_present(project_folder, spec) for spec in specs)
        label = " or ".join(spec.get("label", name) for name, spec in zip(names, specs, strict=True))
        add = next((spec["add"] for spec in specs if spec.get("add")), "")
        message = "" if ok else (
            f"Missing {' or '.join(names)} ({label}), needed by {', '.join(reasons)}."
            + (f" {add}." if add else "")
        )
        findings.append(Finding(key, label, ok, reasons, message))
    for text in unmet_needs(version, processes):
        findings.append(Finding("process", "Process combination", False, [], text))
    from ..morphology.layers.advanced_l0 import missing_model_inputs

    for path in missing_model_inputs(project_folder):
        findings.append(Finding(path, "Namelist input file", False, ["nml-settings.json"],
                                f"Missing {path}, named in the namelist handoff."))
    return findings


# --- Outputs ---------------------------------------------------------------


@cache
def output_registry(version=None) -> dict:
    """outputs.yml: what mHM writes, the analyses and where the switches live."""
    return _yaml("outputs.yml", frozenset(_OUTPUT_KEYS))


def required_outputs(version, analyses) -> dict[str, list[str]]:
    """Map each output an analysis needs to the analyses needing it."""
    registry_ = output_registry(version)
    needed = {}
    for analysis in analyses:
        spec = registry_["analyses"][analysis]
        for output in spec.get("needs", []) + spec.get("any_of", []):
            needed.setdefault(output, []).append(analysis)
    return needed


def switch_key(version, output) -> str | None:
    """The output namelist key that turns an output on (v5.13 keys are 1-based indices)."""
    spec = output_registry(version)["outputs"][output]
    if "switch" not in spec:
        return None
    switches = output_registry(version)["switches"][version_key(version)]
    if version_key(version) == "v5.13":
        # mrm_outputs.nml has no profile in our v5.13 schemas, so Qrouted is not checkable.
        return f"{switches['key']}({spec['switch']})" if isinstance(spec["switch"], int) else None
    return switches.get("keys", {}).get(output) or switches["key"].format(name=spec["nc"])


def output_switches(version, analyses) -> dict[str, bool]:
    """The output-namelist keys to turn on for the chosen analyses."""
    return {key: True for output in required_outputs(version, analyses)
            if (key := switch_key(version, output))}


def _switch_on(version, values, output) -> bool | None:
    key = switch_key(version, output)
    if key is None:
        return None
    switches = output_registry(version)["switches"][version_key(version)]
    if version_key(version) == "v5.13":
        flags = values.get(switches["namelist"], {}).get(switches["key"]) or []
        index = output_registry(version)["outputs"][output]["switch"] - 1
        return bool(flags[index]) if index < len(flags) else False
    namelist, _, name = key.rpartition(".")
    return bool(values.get(namelist or switches["namelist"], {}).get(name.lower(), False))


def _process_ok(processes, needs) -> bool:
    return all(int((processes or {}).get(name, 0)) in cases for name, cases in (needs or {}).items())


def _output_problems(version, values, processes, output) -> list[str]:
    spec = output_registry(version)["outputs"][output]
    problems = []
    if _switch_on(version, values, output) is False:
        problems.append(f"enable output `{output}` ({switch_key(version, output)})")
    if spec.get("needs") and not _process_ok(processes, spec["needs"]):
        problems.extend(f"{_name(name)} must be case {' or '.join(map(str, cases))}"
                        for name, cases in spec["needs"].items())
    return problems


def check_outputs(project_folder, analyses=None) -> list[Finding]:
    """Before a run: is each needed output switched on, and is its process active?"""
    version = layout.project_version(project_folder)
    analyses = analyses or list(output_registry(version)["analyses"])
    values = read_namelists(project_folder)
    processes = current_processes(project_folder) or {}
    findings = []
    for output, users in required_outputs(version, analyses).items():
        problems = _output_problems(version, values, processes, output)
        message = "" if not problems else (
            f"{', '.join(a.replace('_', ' ') for a in users).capitalize()} unavailable: "
            + "; ".join(problems) + ".")
        findings.append(Finding(output, output, not problems, problems, message))
    return findings


def output_step_days(project_folder) -> float | None:
    """The output interval in days from the saved output namelist (0 = whole run)."""
    version = layout.project_version(project_folder)
    switches = output_registry(version)["switches"][version_key(version)]
    values = read_namelists(project_folder)
    step = values.get(switches["namelist"], {}).get(switches["step"])
    if step is None:
        return None
    step = int(step)
    if step < 0:
        return _STEP_DAYS[step]
    if step == 0:
        return 0
    if version_key(version) == "v5.13":
        hours = int(values.get("mainconfig_mhm_mrm", {}).get("timestep", 1))
        return step * hours / 24
    return step / 24


def available_analyses(project_folder, period_days=None) -> dict[str, list[str]]:
    """Analysis -> reasons it is unavailable ([] = available)."""
    version = layout.project_version(project_folder)
    registry_ = output_registry(version)
    by_output = {f.input: f for f in check_outputs(project_folder)}
    step = output_step_days(project_folder)
    result = {}
    for name, spec in registry_["analyses"].items():
        reasons = [problem for o in spec.get("needs", []) if o in by_output for problem in by_output[o].reasons]
        any_of = spec.get("any_of", [])
        if any_of and not any(by_output[o].ok for o in any_of if o in by_output):
            reasons.append(f"needs one of {', '.join(any_of)}")
        if spec.get("min_step") == "day" and step is not None and (step == 0 or step > 1):
            reasons.append("needs daily or finer output")
        if spec.get("min_period_days") and period_days is not None and period_days < spec["min_period_days"]:
            reasons.append(f"needs at least {spec['min_period_days']} days of output")
        result[name] = reasons
    return result
