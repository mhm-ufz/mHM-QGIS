"""The introduction wizard's content and its effects (Qt-free).

`questions.yml` holds the pages; an answer maps a page id to a choice id, or to a
list of ids for `multiple` pages.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

from ..core import datamodel
from ..core.handlers.store.paths import version_key

_PAGE_KEYS = {"id", "kind", "title", "text", "choices", "show_if", "tips"}
_CHOICE_KEYS = {"id", "label", "effects", "tips", "default"}
_KINDS = {"info", "choice", "multiple"}
_DEFAULTS = {"work_type", "l1_factor", "l11_factor"}


def _check_effect(key):
    group, _, name = key.partition(".")
    known = {
        "processes": datamodel.load_rules("6")["processes"],
        "defaults": _DEFAULTS,
        "analyses": datamodel.output_registry()["analyses"],
        "available": datamodel.load_rules("6")["inputs"],
    }.get(group)
    if known is None or name not in known:
        raise ValueError(f"questions.yml: unknown effect {key!r}")


@cache
def load_questions() -> dict:
    """The validated wizard schema."""
    data = yaml.safe_load(Path(__file__).with_name("questions.yml").read_text(encoding="utf-8"))
    for page in data["pages"]:
        if set(page) - _PAGE_KEYS or page.get("kind") not in _KINDS:
            raise ValueError(f"questions.yml: page {page.get('id')!r} has unknown keys or kind")
        for choice in page.get("choices", []):
            if set(choice) - _CHOICE_KEYS:
                raise ValueError(f"questions.yml: choice {choice.get('id')!r} has unknown keys")
            for key in choice.get("effects", {}):
                _check_effect(key)
    for key in data.get("derive", {}):
        _check_effect(key)
    return data


def visible(page, answers) -> bool:
    """`show_if` maps a page id to the choice ids that show this page."""
    for other, ids in (page.get("show_if") or {}).items():
        picked = answers.get(other)
        picked = picked if isinstance(picked, list) else [picked]
        if not set(picked) & set(ids):
            return False
    return True


def chosen(questions, answers) -> list[dict]:
    """The selected choices of the visible pages."""
    selected = []
    for page in questions["pages"]:
        if not visible(page, answers):
            continue
        picked = answers.get(page["id"])
        ids = picked if isinstance(picked, list) else [picked]
        selected.extend(c for c in page.get("choices", []) if c["id"] in ids)
    return selected


def _derived(questions, merged, version):
    available = {key.split(".", 1)[1] for key, value in merged.items()
                 if key.startswith("available.") and value}
    feasible = datamodel.feasible_cases(version, available)
    result, notes = {}, []
    for key, rule in (questions.get("derive") or {}).items():
        prefer = rule["prefer"]
        prefer = prefer.get(version_key(version)) if isinstance(prefer, dict) else prefer
        process = key.split(".", 1)[1]
        case = next((c for c in prefer or [] if c in feasible.get(process, [])), None)
        if case is None:
            notes.append(rule.get("none", f"No case of {process} fits the data."))
            case = (rule.get("fallback") or {}).get(version_key(version))
        if case is not None:
            result[key] = case
    return result, notes


def effects(questions, answers, version) -> dict:
    """Merged effects of the chosen answers, with the derived process cases."""
    merged = {}
    for choice in chosen(questions, answers):
        merged.update(choice.get("effects", {}))
    derived, _notes = _derived(questions, merged, version)
    return {**merged, **derived}


def tips(questions, answers, version) -> list[str]:
    """Tips of the chosen answers, then why a derived process could not be set."""
    notes = [tip for choice in chosen(questions, answers) for tip in choice.get("tips", [])]
    merged = {}
    for choice in chosen(questions, answers):
        merged.update(choice.get("effects", {}))
    return notes + _derived(questions, merged, version)[1]


def summary(version, effects_) -> list[str]:
    """What the chosen processes read, and which of it was not ticked as available."""
    processes = {key.split(".", 1)[1]: value for key, value in effects_.items()
                 if key.startswith("processes.")}
    rules = datamodel.load_rules(version)
    available = {key.split(".", 1)[1] for key, value in effects_.items()
                 if key.startswith("available.") and value}
    lines = [(label := datamodel.case_label(version, process, case))[0].upper() + label[1:]
             for process, case in processes.items()]
    needed = datamodel.required_inputs(version, processes)
    meteo = [name for key in needed for name in key.split("|")
             if "meteo" in rules["inputs"][name].get("locate", {})]
    if meteo:
        lines.append("Forcing you will prepare: " + ", ".join(meteo))
    missing = [name for name in meteo if name not in available]
    if missing:
        lines.append("Not ticked as available: " + ", ".join(missing))
    return lines + datamodel.unmet_needs(version, processes)


def handoff(version, effects_, saved=None, project="classic") -> dict[str, dict[str, dict]]:
    """The wizard's process and output choices as `{profile: {namelist: values}}`.

    v5.13 keeps both as one-based arrays, so they are merged into the saved
    namelist's lists (`saved` is `datamodel.read_namelists`), or the schema example.
    In a v6 combined setup (`project` "combine") the processes go to `meta`.
    """
    saved = saved or {}
    processes = {key.split(".", 1)[1]: int(value) for key, value in effects_.items()
                 if key.startswith("processes.")}
    analyses = [key.split(".", 1)[1] for key, value in effects_.items()
                if key.startswith("analyses.") and value]
    switches = datamodel.output_switches(version, analyses)
    if version_key(version) == "v6":
        output: dict[str, dict] = {}
        for key in switches:
            namelist, _, name = key.rpartition(".")
            output.setdefault(namelist or "output_mhm", {})[name] = True
        result = {"output": output} if output else {}
        if processes:
            result["meta" if project == "combine" else "main"] = {"config_processes": processes}
        return result
    result = {}
    if processes:
        rules = datamodel.load_rules(version)
        cases = list(saved.get("processselection", {}).get("processcase") or _v5_example_cases())
        for process, case in processes.items():
            cases[rules["processes"][process]["v5_13_index"] - 1] = case
        result["main"] = {"processSelection": {"processCase": cases}}
    routed = [key for key in switches if "." in key]  # nloutputresults_mrm.outputflxstate_mrm(1)
    if routed:
        flags = list(saved.get("nloutputresults_mrm", {}).get("outputflxstate_mrm") or [False, False])
        for key in routed:
            flags[int(key.split("(")[1].rstrip(")")) - 1] = True
        result["mrm_output"] = {"NLoutputResults_mrm": {"outputFlxState_mrm": flags}}
    if len(routed) < len(switches):
        size = max(spec["switch"] for spec in datamodel.output_registry()["outputs"].values()
                   if isinstance(spec.get("switch"), int))
        flags = list(saved.get("nloutputresults", {}).get("outputflxstate") or [])
        flags += [False] * (size - len(flags))
        for key in switches:
            if key not in routed:
                flags[int(key.split("(")[1].rstrip(")")) - 1] = True
        result["output"] = {"NLoutputResults": {"outputFlxState": flags}}
    return result


def _v5_example_cases() -> list[int]:
    from ..core.handlers.store.paths import schemas_folder

    path = schemas_folder("5.13") / "nml-schemas" / "config_process_selection.yml"
    schema = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(schema["properties"]["processCase"]["examples"][0])
