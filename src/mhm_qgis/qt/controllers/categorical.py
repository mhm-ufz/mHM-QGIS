# -*- coding: utf-8 -*-
"""Land cover, soil, geology and LAI input types on the main form.

Choosing an input type opens its child dialog at once. Accepting stores the
chosen source in one of the dialog's config dicts, which are the only record of
it; cancelling restores the previous type.
"""
from __future__ import annotations

import os
from pathlib import Path

from qgis.PyQt.QtWidgets import QDialog, QMessageBox

from ...core.handlers.state.nml_settings import relative_workspace_path, update_section
from ...core.handlers.store.layout import lai_folder, morph_folder
from ..dialogs import advanced_inputs
from ..dialogs.lai_netcdf_input import LaiNetcdfInputDialog
from ..dialogs.mhm_ready_morph_input import MhmReadyInputDialog
from ..dialogs.single_layer_input import SingleLayerInputDialog
from .grid import require_common_grid
from .main import refresh_morphology_date_control
from .workflow_state import invalidate_meteo

KINDS = ("lc", "soil", "geology", "lai")
_LABELS = {"lc": "land use", "soil": "soil", "geology": "geology", "lai": "LAI"}


def categorical_type_combo(dialog, kind):
    return {
        "lc": dialog.comboBox_landUseInputType,
        "soil": dialog.comboBox_soil_inputType,
        "geology": dialog.comboBox_geology_inputType,
        "lai": dialog.comboBox_lai_inputType,
    }[kind]


def categorical_input_mode(dialog, kind):
    return categorical_type_combo(dialog, kind).currentText().strip()


def categorical_source_config(dialog, kind):
    """Return the dialog-owned source for a categorical workflow."""
    if categorical_input_mode(dialog, kind).lower() == "mhm ready":
        config = dialog._categorical_ready_configs.get(kind)
    else:
        config = dialog._categorical_lookup_configs.get(kind)
    return dict(config) if isinstance(config, dict) else None


def uses_advanced_categorical_input(dialog, kind):
    """Return whether processing should use the advanced input pipeline."""
    text = categorical_input_mode(dialog, kind).lower()
    return (
        (kind == "lc" and "historical" in text)
        or (kind == "soil" and "multi-horizon" in text)
    )


def restore_categorical_mode(dialog, kind, text):
    combo = categorical_type_combo(dialog, kind)
    combo.blockSignals(True)
    combo.setCurrentIndex(combo.findText(text) if text else -1)
    combo.blockSignals(False)


def handle_categorical_type(dialog, kind, text):
    """Open the child dialog for the selected input type, then record it."""
    text = str(text or "").strip()
    previous = dialog._categorical_modes.get(kind, "")
    if dialog._loading_input_state:
        dialog._categorical_modes[kind] = text
        return
    if not dialog.project_folder:
        QMessageBox.warning(
            dialog,
            "Project Folder Required",
            f"Select a project folder before configuring {_LABELS[kind]} inputs.",
        )
        restore_categorical_mode(dialog, kind, previous)
        return
    mode = text.lower()
    if mode == "mhm ready":
        accepted = _configure_ready(dialog, kind, previous)
    elif kind == "lai" and "netcdf" in mode:
        accepted = _configure_lai_netcdf(dialog, previous)
    elif kind == "lc" and "single categorical" not in mode:
        accepted = _configure_historical_land_use(dialog, text, previous)
    elif kind == "soil" and "multi-horizon" in mode:
        accepted = _configure_multi_horizon_soil(dialog, previous)
    elif kind in ("lc", "lai") or "lookup table" in mode:
        accepted = _configure_lookup(dialog, kind, previous)
    else:
        accepted = True
    if not accepted:
        return
    dialog._categorical_modes[kind] = text
    dialog.save_input_state()
    invalidate_meteo(dialog)
    if kind == "lc":
        refresh_morphology_date_control(dialog)
    if mode == "mhm ready":
        title = f'{"LAI" if kind == "lai" else kind.title()} ready input'
        dialog.morphology_tasks.start_ready_input(
            kind, failed=lambda message: QMessageBox.critical(dialog, title, str(message)),
        )


def _accepted(dialog, kind, previous, child, result="selected_config"):
    """Run a child dialog; restore the previous type unless it yields a value."""
    value = getattr(child, result)() if child.exec() == QDialog.DialogCode.Accepted else None
    if value is None:
        restore_categorical_mode(dialog, kind, previous)
    return value


def _configure_ready(dialog, kind, previous):
    try:
        require_common_grid(dialog)
    except ValueError as error:
        QMessageBox.warning(dialog, "Common Grid Required", str(error))
        restore_categorical_mode(dialog, kind, previous)
        return False
    config = _accepted(dialog, kind, previous, MhmReadyInputDialog(
        dialog.project_folder, kind, dialog,
        initial=dialog._categorical_ready_configs.get(kind),
    ))
    if config is None:
        return False
    dialog._categorical_ready_configs[kind] = {
        "input_path": config.input_path,
        "classdefinition_path": getattr(config, "classdefinition_path", ""),
        "variable": getattr(config, "variable", ""),
        "source_crs": getattr(config, "source_crs", ""),
    }
    dialog._categorical_lookup_configs.pop(kind, None)
    if kind == "lai":
        dialog._lai_input_config = {}
    elif kind == "lc":
        dialog._advanced_inputs.pop("land_cover", None)
        dialog._land_cover_ready_source = config.input_path
    elif kind == "soil":
        dialog._advanced_inputs.pop("soil", None)
        save_standard_soil_nml_input(dialog, "mhm_ready")
    return True


def _configure_lookup(dialog, kind, previous):
    config = _accepted(dialog, kind, previous, SingleLayerInputDialog(
        dialog.project_folder, kind, dialog,
        initial=dialog._categorical_lookup_configs.get(kind),
    ))
    if config is None:
        return False
    dialog._categorical_lookup_configs[kind] = {
        "input_path": config.input_path,
        "lookup_table": config.lookup_table,
        "mapping_field": config.mapping_field,
        "class_field": config.class_field,
        "variable": getattr(config, "variable", ""),
        "source_crs": getattr(config, "source_crs", ""),
    }
    dialog._categorical_ready_configs.pop(kind, None)
    if kind == "lai":
        dialog._lai_input_config = {}
        update_section(dialog.project_folder, "lai", {
            "mode": "single_categorical",
            **dialog._categorical_lookup_configs["lai"],
            "processing_status": "not_implemented",
        })
    elif kind == "lc":
        dialog._advanced_inputs.pop("land_cover", None)
        dialog._land_cover_ready_source = ""
    elif kind == "soil":
        dialog._advanced_inputs.pop("soil", None)
        save_standard_soil_nml_input(dialog, "single_categorical")
    return True


def _configure_lai_netcdf(dialog, previous):
    from ...externals.mhm_tools_handler import lai_time_step

    config = _accepted(dialog, "lai", previous, LaiNetcdfInputDialog(
        dialog.project_folder, dialog, initial=dialog._lai_input_config,
    ))
    if config is None:
        return False
    dialog._lai_input_config = {
        "input_path": config.input_path,
        "target_timestep": config.target_timestep,
        "variable": getattr(config, "variable", ""),
        "source_crs": getattr(config, "source_crs", ""),
    }
    dialog._categorical_lookup_configs.pop("lai", None)
    update_section(dialog.project_folder, "lai", {
        "mode": "netcdf",
        "source_path": config.input_path,
        "source_variable": config.variable,
        "target_timestep": config.target_timestep,
        "time_step": lai_time_step(config.target_timestep),
        "output_path": relative_workspace_path(
            dialog.project_folder, os.path.join(lai_folder(dialog.project_folder), "lai.nc")
        ),
        "variable": "lai",
    })
    return True


def _configure_historical_land_use(dialog, text, previous):
    value = _accepted(dialog, "lc", previous, advanced_inputs.HistoricalLandUseDialog(
        dialog.project_folder, dialog,
        initial=dialog._advanced_inputs.get("land_cover"),
        all_time="single layer" in text.lower(),
    ), "selected_input")
    if value is None:
        return False
    dialog._advanced_inputs["land_cover"] = value
    dialog._land_cover_ready_source = ""
    save_land_cover_nml_input(dialog, value)
    return True


def _configure_multi_horizon_soil(dialog, previous):
    value = _accepted(dialog, "soil", previous, advanced_inputs.MultiHorizonSoilDialog(
        dialog.project_folder, dialog, initial=dialog._advanced_inputs.get("soil"),
    ), "selected_input")
    if value is None:
        return False
    dialog._advanced_inputs["soil"] = value
    save_soil_nml_input(dialog, value)
    return True


# --- Namelist handoff -----------------------------------------------------

def save_land_cover_nml_input(dialog, value):
    update_section(dialog.project_folder, "land_cover", {
        "mode": "historical" if len(value.periods) > 1 else "single",
        "variable": "land_cover",
        "lookup_table": str(value.lookup_table),
        "mapping_field": value.mapping_field,
        "class_field": value.class_field,
        "scenes": [
            {
                "start_time": item.start_time.isoformat(),
                "end_time": item.end_time.isoformat(),
                "start_year": item.start_year,
                "end_year": item.end_year,
                "source_path": str(item.file_path),
            }
            for item in value.periods
        ],
    })


def save_soil_nml_input(dialog, value):
    data = value.as_dict()
    data.update({
        "mode": "multi_horizon",
        "soil_db_mode": 1,
        "variable": "soil_class",
        "source_bulk_density_unit": value.bulk_density_unit,
        "bulk_density_unit": "g/cm3",
        "composition_normalization": "component_sum_percent",
    })
    update_section(dialog.project_folder, "soil", data)


def save_standard_soil_nml_input(dialog, mode, output_path=None, classdefinition_path=None):
    if not dialog.project_folder:
        return
    folder = morph_folder(dialog.project_folder)
    update_section(dialog.project_folder, "soil", {
        "mode": mode,
        "soil_db_mode": 0,
        "output_path": relative_workspace_path(
            dialog.project_folder, output_path or os.path.join(folder, "soil_class.asc")
        ),
        "variable": "soil_class",
        "classdefinition_path": relative_workspace_path(
            dialog.project_folder,
            classdefinition_path or os.path.join(folder, "soil_classdefinition.txt"),
        ),
        "horizons": [],
    })


def record_standard_soil_output(dialog, output_path=None, classdefinition_path=None):
    """Update the namelist handoff after standard soil preparation."""
    save_standard_soil_nml_input(
        dialog,
        "mhm_ready" if categorical_input_mode(dialog, "soil").lower() == "mhm ready"
        else "single_categorical",
        output_path,
        classdefinition_path,
    )


def refresh_advanced_nml_settings(dialog):
    """Refresh version-dependent settings after the mHM version changes."""
    value = dialog._advanced_inputs.get("soil")
    if value:
        save_soil_nml_input(dialog, soil_input_value(value))
    land_cover = dialog._advanced_inputs.get("land_cover")
    if land_cover:
        save_land_cover_nml_input(dialog, land_use_input_value(land_cover))
    invalidate_meteo(dialog)


def advanced_input(dialog, kind):
    """Return the advanced land-cover (`lc`) or soil input as its typed value."""
    if kind == "lc":
        return land_use_input_value(dialog._advanced_inputs.get("land_cover"))
    return soil_input_value(dialog._advanced_inputs.get("soil"))


def land_use_input_value(value):
    from ...core.handlers.lookup import LandUseInput, LandUsePeriod

    if isinstance(value, LandUseInput):
        return value
    if not isinstance(value, dict):
        raise ValueError("Configure the land-use layers first.")
    return LandUseInput(
        tuple(LandUsePeriod.from_dict(item) for item in value.get("periods", [])),
        Path(value.get("lookup_table", "")),
        str(value.get("mapping_field", "")),
        str(value.get("class_field", "")),
    )


def soil_input_value(value):
    from ...core.handlers.lookup import SoilHorizon, SoilInput

    if isinstance(value, SoilInput):
        return value
    if not isinstance(value, dict):
        raise ValueError("Configure the multi-horizon soil layers first.")
    return SoilInput(
        tuple(
            SoilHorizon(
                int(item["horizon"]),
                float(item["upper_depth"]),
                float(item["lower_depth"]),
                Path(item["clay_layer"]),
                Path(item["sand_layer"]),
                Path(item["silt_layer"]),
                Path(item["bulk_density_layer"]),
            )
            for item in value.get("horizons", [])
        ),
        str(value.get("bulk_density_unit", "")),
        composition_step=float(value.get("composition_step", 5.0)),
        bulk_density_step=float(value.get("bulk_density_step", 0.1)),
    )
