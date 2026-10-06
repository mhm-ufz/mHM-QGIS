"""The introduction wizard: run it, then apply its answers to the form and namelists.

Form defaults apply at once. Process and output choices go into the setup's
one-shot `intro_values` handoff; with no setup open yet they wait for the next one
(`write_pending`), and `model.edit_namelists` drops them once a namelist is saved.
"""
from __future__ import annotations

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QDialog

from ... import intro
from ...core import datamodel
from ...core.handlers.state import nml_settings
from . import grid
from .version import selected as selected_version


def launch(dialog):
    from ..dialogs.intro_wizard import IntroWizard

    wizard = IntroWizard(intro.load_questions(), selected_version(dialog), dialog._intro_answers, dialog)
    if wizard.exec() != QDialog.DialogCode.Accepted:
        return False
    dialog._intro_answers = wizard.answers()
    dialog._intro_pending = True
    apply(dialog)
    dialog.save_input_state()
    return True


def apply(dialog):
    version = selected_version(dialog)
    effects = intro.effects(intro.load_questions(), dialog._intro_answers, version)
    _apply_work_type(dialog, effects.get("defaults.work_type"))
    _apply_resolutions(dialog, effects)
    _mark_meteo_variables(dialog, version, effects)
    for tip in intro.tips(intro.load_questions(), dialog._intro_answers, version):
        dialog.log_message(f"Introduction: {tip}")
    write_pending(dialog)


def _apply_work_type(dialog, index):
    if index is None:
        return
    item = dialog.comboBox_mhmWorkType.model().item(index)
    if item is not None and item.isEnabled():
        dialog.comboBox_mhmWorkType.setCurrentIndex(index)
    else:
        dialog.log_message("Introduction: combining domains is not available yet; "
                           "the single-domain setup stays selected.")


def _apply_resolutions(dialog, effects):
    l0 = grid.current_l0_resolution(dialog)
    if not l0:
        if "defaults.l1_factor" in effects:
            dialog.log_message("Introduction: the L1/L11 resolutions are suggested once a DEM is chosen; "
                               "run the wizard again then, or pick them from the list.")
        return
    if "defaults.l1_factor" in effects:
        dialog._preferred_l1_resolution = l0 * effects["defaults.l1_factor"]
    if "defaults.l11_factor" in effects:
        dialog._preferred_l11_resolution = l0 * effects["defaults.l11_factor"]
    grid.refresh_l1_l11_resolution_options(dialog)


def _mark_meteo_variables(dialog, version, effects):
    """Tell which forcing the chosen processes read, and select the first one still missing."""
    processes = {key.split(".", 1)[1]: value for key, value in effects.items()
                 if key.startswith("processes.")}
    needed = datamodel.required_inputs(version, processes)
    rules = datamodel.load_rules(version)
    reasons = {}
    for key, why in needed.items():
        for name in key.split("|"):
            meteo = rules["inputs"][name].get("locate", {}).get("meteo")
            if meteo:
                reasons.setdefault(meteo, []).extend(why)
    combo = dialog.comboBox_meteoVar
    first = None
    for index in range(combo.count()):
        name = combo.itemText(index)
        why = reasons.get(name)
        combo.setItemData(index, f"Needed by {', '.join(why)}." if why else "", Qt.ItemDataRole.ToolTipRole)
        if why and first is None and name not in dialog._meteo_inputs:
            first = index
    if first is not None:
        combo.setCurrentIndex(first)


def write_pending(dialog):
    """Hand the wizard's process and output choices to the open setup, once."""
    if not dialog._intro_pending or not dialog.project_folder:
        return
    version = selected_version(dialog)
    effects = intro.effects(intro.load_questions(), dialog._intro_answers, version)
    try:
        saved = datamodel.read_namelists(dialog.project_folder)
    except ValueError as error:
        dialog.log_message(f"WARNING: {error}; the wizard's choices start from the defaults.")
        saved = {}
    values = intro.handoff(version, effects, saved)
    nml_settings.update_section(dialog.project_folder, "intro_values", values)
    dialog._intro_pending = False
    if values:
        dialog.log_message("Introduction: process and output choices are ready for Edit Namelists.")
