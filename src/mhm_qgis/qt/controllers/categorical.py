"""Land cover, soil, geology and LAI inputs on their project-tree pages.

A radio of `groupBox_<page>InputType` (texts as `MODES`) shows the matching page of
`stackedWidget_<page>`; the page's Set reads its widgets into a typed config, which the
`_configure_*` functions store in the dialog's config dicts (the only record of it), and
commits the type.
"""
from __future__ import annotations

import os
from pathlib import Path

from qgis.PyQt.QtWidgets import QMessageBox, QPushButton, QTableWidgetItem

from ...core.handlers import lookup
from ...core.handlers.state.nml_settings import relative_workspace_path, update_section
from ...core.handlers.store.layout import lai_folder, morph_folder
from ...core.morphology.layers.categorical import ready_layers
from ..dialogs.input_selection import (
    InputComboAdapter,
    LaiNetcdfConfig,
    ReadyInputConfig,
    SingleLayerLookupConfig,
    _browse_into_combo,
    _browse_raster_into_combo,
    _combo_path,
    _combo_raster_details,
    _ensure_raster_details,
    _populate_file_combo,
    _populate_layer_combo,
    _restore_raster_details,
)
from . import main
from .grid import require_common_grid
from .soil_horizons import SoilHorizonInputs
from .workflow_state import invalidate, invalidate_meteo

KINDS = ("lc", "soil", "geology", "lai")
_LABELS = {"lc": "land use", "soil": "soil", "geology": "geology", "lai": "LAI"}
PAGES = {"lc": "landUse", "soil": "soil", "geology": "geology", "lai": "lai"}
_SOURCES = {"lc": "land_cover", "soil": "soil", "geology": "geology", "lai": "lai"}
_READY, _LOOKUP = "mHM ready", "Single Categorical Layer (with Lookup Table)"
_PERIODS = "Categorical Periods (with Lookup Table)"
MODES = {  # in the order of the radios and of the stacked pages
    "lc": (_READY, _PERIODS),
    "soil": (_READY, "Categorical Layer (with Lookup Table)", "Multi-horizon Multi-variate Layers"),
    "geology": (_READY, _LOOKUP),
    "lai": ("mHM ready (Class Layer)", "NetCDF File", _LOOKUP),
}
_OLD_MODES = {  # texts saved before the types moved onto the main form
    ("lc", _LOOKUP.lower()): _PERIODS,
    ("lc", "multi historical periods (with lookup table)"): _PERIODS,
    ("soil", _LOOKUP.lower()): MODES["soil"][1],
    ("lai", "mhm ready"): MODES["lai"][0],
}
# class file combo, its browse, class definition combo, its browse
_READY_WIDGETS = {
    "lc": ("comboBox_demInput_mhmReadyLandUseInput", "pushButton_browseMhmReadyLandUseInput", None, None),
    "soil": ("comboBox_demInput_mhmReadySoilInput", "pushButton_browseMhmReadySoilInput",
             "comboBox_soilClassDefinitionInputFile", "pushButton_browseSoilClassDefinitionFile"),
    "geology": ("comboBox_mhmReadyGeologyClassInput", "pushButton_browseMhmReadyGeologyClassFile",
                "comboBox_geologyClassDefinitionInputFile", "pushButton_browseGeologyClassDefinitionFile"),
    "lai": ("comboBox_mhmReadyLaiClassInput", "pushButton_browseMhmReadyLaiClassFile",
            "comboBox_laiClassDefinitionInputFile", "pushButton_laiGeologyClassDefinitionFile"),
}
# layer combo, its browse, lookup combo, its browse, mapping field, class field
_LOOKUP_WIDGETS = {
    "lc": ("comboBox_demInput_categoricalLandUseInput", "pushButton_browseCategoricalLandUseInput",
           "comboBox_lookupTableLandUse", "pushButton_browseLookupTableLandUse",
           "comboBox_mappingFieldLandUse", "comboBox_classFieldLandUse"),
    "soil": ("comboBox_categoricalSoilInput", "pushButton_browseCategoricalSoilInput",
             "comboBox_lookupTableSoil", "pushButton_browseLookupTableSoil",
             "comboBox_mappingFieldSoil", "comboBox_classFieldSoil"),
    "geology": ("comboBox_inputGeologyCategoricalLayer", "pushButton_browseGeologyCategoricalLayer",
                "comboBox_lookupTableGeology", "pushButton_browseLookupTableGeology",
                "comboBox_mappingFieldGeology", "comboBox_classFieldGeology"),
    "lai": ("comboBox_inputLaiCategoricalLayer", "pushButton_browseLaiCategoricalLayer",
            "comboBox_lookupTableLai", "pushButton_browseLookupTableLai",
            "comboBox_mappingFieldGeologyLai", "comboBox_classFieldLai"),
}
# a layer table: the table, its layer combo, the two values of a row (dates or depths in mm) and Add
_TABLES = {
    "lc_lookup": ("tableWidget_categoricalLandUseInput", "comboBox_demInput_categoricalLandUseInput",
                  "dateTimeEdit_categoricalLandUseInputStart", "dateTimeEdit_categoricalLandUseInputEnd",
                  "pushButton_categoricalLandUseInputAdd"),
    "lc_ready": ("tableWidget_mhmReadyLandUseInput", "comboBox_demInput_mhmReadyLandUseInput",
                 "dateTimeEdit_mhmReadyLandUseInputStart", "dateTimeEdit_mhmReadyLandUseInputEnd",
                 "pushButton_mhmReadyLandUseInputAdd"),
    "soil_ready": ("tableWidget_mhmReadySoilInput", "comboBox_demInput_mhmReadySoilInput",
                   "lineEdit_upperDepth", "lineEdit_lowerDepth", "pushButton_mhmReadySoilInputAdd"),
    "soil_lookup": ("tableWidget_categoricalSoilInput", "comboBox_categoricalSoilInput",
                    "lineEdit_categoricalSoilUpperDepth", "lineEdit_categoricalSoilLowerDepth", "pushButton_categoricalSoilInputAdd"),
}
# a multi-layer switch (off by default) → its table and the labels it enables beside the table's widgets
_SWITCHES = {
    "checkBox_multiPeriodCategoricalLandUseInput": ("lc_lookup", ()),
    "checkBox_multiPeriodmhmReadyLandUseInput": ("lc_ready", ("label_mhmReadyLandUseFrom", "label_mhmReadyLandUseTo")),
    "checkBox_multiClassLayerSoil": ("soil_ready", ("label_mhmReadySoilUpperDepth", "label_mhmReadySoilLowerDepth")),
    "checkBox_multiClassLayerCategoricalSoil": ("soil_lookup", ("label_categoricalSoilUpperDepth",
                                                        "label_categoricalSoilLowerDepth")),
}
_SWITCH = {key: checkbox for checkbox, (key, _labels) in _SWITCHES.items()}
_RASTER_FILTER = "Raster files (*.asc *.nc *.tif *.tiff);;All files (*)"
_LAYER_FILTER = "Input layers (*.asc *.nc *.gpkg *.shp *.tif *.tiff);;All files (*)"
_TABLE_FILTER = "Tables (*.csv *.txt);;All files (*)"


def is_ready(text) -> bool:
    return str(text or "").lower().startswith("mhm ready")


def saved_mode(kind, text) -> str:
    """The type for a saved text, in any case, with `_` for spaces or from an older version."""
    normalized = str(text or "").lower().replace("_", " ")
    return _OLD_MODES.get((kind, normalized)) or next((mode for mode in MODES[kind] if mode.lower() == normalized), "")


def _widgets(dialog, names):
    return tuple(getattr(dialog, name) if name else None for name in names)


def bind(dialog):
    """Radios show their page; browse buttons fill their combos; Set commits."""
    dialog._lookup_adapters = {}
    for kind in KINDS:
        main.radios(dialog, PAGES[kind], lambda text, kind=kind: show_type(dialog, kind, text))
        combo, browse, definition, browse_definition = _widgets(dialog, _READY_WIDGETS[kind])
        browse.clicked.connect(lambda _checked=False, combo=combo: _browse_raster_into_combo(
            dialog, combo, "Select mHM-ready input", _RASTER_FILTER))
        if definition is not None:
            browse_definition.clicked.connect(lambda _checked=False, combo=definition: _browse_into_combo(
                dialog, combo, "Select class definition", _TABLE_FILTER))
        layer, browse_layer, table, browse_table, mapping, class_field = _widgets(dialog, _LOOKUP_WIDGETS[kind])
        dialog._lookup_adapters[kind] = InputComboAdapter(layer, _SOURCES[kind], dialog)
        browse_layer.clicked.connect(lambda _checked=False, combo=layer: _browse_raster_into_combo(
            dialog, combo, "Select categorical input", _LAYER_FILTER))
        browse_table.clicked.connect(lambda _checked=False, combo=table: _browse_into_combo(
            dialog, combo, "Select lookup table", _TABLE_FILTER))
        table.currentIndexChanged.connect(lambda _index, fields=(table, mapping, class_field): _refresh_fields(*fields))
        getattr(dialog, f"pushButton_set{PAGES[kind][0].upper()}{PAGES[kind][1:]}").clicked.connect(
            lambda _checked=False, kind=kind: set_input(dialog, kind))
    dialog.pushButton_browseLAINetCDFInput.clicked.connect(lambda: _browse_raster_into_combo(
        dialog, dialog.comboBox_laiNetCDFInput, "Select LAI NetCDF", "NetCDF (*.nc);;All files (*)"))
    dialog._soil_horizons = SoilHorizonInputs(dialog)
    dialog._layer_rows = {key: [] for key in _TABLES}
    for checkbox, (key, _labels) in _SWITCHES.items():
        getattr(dialog, _TABLES[key][4]).clicked.connect(lambda _checked=False, key=key: add_row(dialog, key))
        getattr(dialog, checkbox).toggled.connect(lambda _on, checkbox=checkbox: _switched(dialog, checkbox))
        _switched(dialog, checkbox)


def show_type(dialog, kind, text):
    """The stacked page of the type `text`; nothing is committed before Set."""
    if text in MODES[kind]:
        getattr(dialog, f"stackedWidget_{PAGES[kind]}").setCurrentIndex(MODES[kind].index(text))


def _checked(dialog, kind) -> str:
    button = getattr(dialog, f"buttonGroup_{PAGES[kind]}").checkedButton()
    return button.text() if button is not None else ""


def _refresh_fields(table, mapping, class_field, preferred=("", "")):
    """The lookup table's columns in both field combos, keeping the chosen ones."""
    wanted = [preferred[0] or mapping.currentText(), preferred[1] or class_field.currentText()]
    try:
        columns = lookup.columns(_combo_path(table)) if _combo_path(table) else []
    except Exception:
        columns = []
    for combo, name in zip((mapping, class_field), wanted, strict=True):
        combo.clear()
        combo.addItems(columns)
        combo.setCurrentIndex(combo.findText(name))


def categorical_input_mode(dialog, kind):
    return dialog._categorical_modes.get(kind, "")


def categorical_source_config(dialog, kind):
    """Return the dialog-owned source for a categorical workflow."""
    if is_ready(categorical_input_mode(dialog, kind)):
        config = dialog._categorical_ready_configs.get(kind)
    else:
        config = dialog._categorical_lookup_configs.get(kind)
    return dict(config) if isinstance(config, dict) else None


def uses_advanced_categorical_input(dialog, kind):
    """Return whether processing should use the advanced input pipeline: land use with
    several Set periods, multi-horizon multi-variate soil."""
    text = categorical_input_mode(dialog, kind)
    return ((kind == "lc" and text == _PERIODS and "land_cover" in dialog._advanced_inputs)
            or (kind == "soil" and "multi-horizon" in text.lower()))


# --- Set ------------------------------------------------------------------

def set_input(dialog, kind):
    """The page's Set: read the checked type's widgets, apply them, commit the type."""
    text, title = _checked(dialog, kind), f"{_LABELS[kind][0].upper()}{_LABELS[kind][1:]} input"
    if not dialog.project_folder:
        QMessageBox.warning(dialog, "Project Folder Required",
                            f"Select a project folder before configuring {_LABELS[kind]} inputs.")
        return
    if not text:
        QMessageBox.warning(dialog, title, "Choose an input type first.")
        return
    try:
        value, apply = _read(dialog, kind, text)
    except ValueError as error:
        QMessageBox.warning(dialog, title, str(error))
        return
    if value is None or not apply(dialog, kind, value):
        return
    dialog._categorical_modes[kind] = text
    invalidate(dialog, "execute_all_morphology")  # A's ▶ is due again
    dialog.save_input_state()
    invalidate_meteo(dialog)
    from .. import project_tree

    project_tree.refresh(dialog)
    if is_ready(text):
        dialog.morphology_tasks.start_ready_input(
            kind, failed=lambda message: QMessageBox.critical(dialog, title, str(message)))


def _read(dialog, kind, text):
    """(typed value, its `_configure_*`) for the checked type; None when a prompt was cancelled."""
    mode = text.lower()
    if is_ready(text):
        return _ready(dialog, kind), _configure_ready
    if kind == "lai" and "netcdf" in mode:
        return _lai_netcdf(dialog), _configure_lai_netcdf
    if kind == "soil" and "multi-horizon" in mode:
        value = dialog._soil_horizons.selected_input()
        depth = value.lower_depths[-1]
        if depth < 100 and QMessageBox.question(
                dialog, "Shallow Soil", f"The deepest soil horizon ends at {depth:g} mm. "
                "Soil depths are in mm; continue anyway?") != QMessageBox.StandardButton.Yes:
            return None, None
        return value, _configure_multi_horizon_soil
    if kind == "lc" and _multi(dialog, "lc_lookup"):
        return _land_use_periods(dialog), _configure_historical_land_use
    multi = kind == "soil" and _multi(dialog, "soil_lookup")
    return (_soil_horizon_lookup(dialog) if multi else _lookup(dialog, kind)), _configure_lookup


def _ready(dialog, kind):
    combo, _, definition, _ = _widgets(dialog, _READY_WIDGETS[kind])
    source, defined = _combo_path(combo), _combo_path(definition) if definition is not None else ""
    key = f"{kind}_ready"
    if key in _TABLES and _multi(dialog, key):  # periods or horizons, each already a ready class grid
        if kind == "soil" and not defined:
            raise ValueError("Several soil horizon class layers need their class definition file.")
        layers = _rows(dialog, key)
        return ReadyInputConfig(layers[0][0], defined, layers=layers)
    if not source or not Path(source).is_file():
        raise ValueError("Select an existing mHM-ready file.")
    if defined and not Path(defined).is_file():
        raise ValueError("Select an existing class definition file.")
    if not _ensure_raster_details(dialog, combo):
        return None
    details = _combo_raster_details(combo)
    return ReadyInputConfig(source, defined, details.get("variable", ""), details.get("source_crs", ""))


def _lookup(dialog, kind):
    """A single categorical layer and its lookup table (a QGIS layer or a file, vectors too)."""
    _, _, table, _, mapping, class_field = _widgets(dialog, _LOOKUP_WIDGETS[kind])
    adapter = dialog._lookup_adapters[kind]
    source, lookup_table = adapter.source_path(), _combo_path(table)
    layer = adapter.currentLayer()
    if not source or not ((layer is not None and layer.isValid()) or Path(source.split("|", 1)[0]).is_file()):
        raise ValueError("Select a readable categorical input layer.")
    if not lookup_table or not Path(lookup_table).is_file():
        raise ValueError("Select an existing lookup table.")
    if not mapping.currentText().strip() or not class_field.currentText().strip():
        raise ValueError("Choose the lookup table's mapping and class fields.")
    if Path(source).suffix.lower() in {".asc", ".nc"} and not _ensure_raster_details(dialog, adapter.combo_box):
        return None
    details = _combo_raster_details(adapter.combo_box)
    return SingleLayerLookupConfig(source, lookup_table, mapping.currentText().strip(),
                                   class_field.currentText().strip(), details.get("variable", ""),
                                   details.get("source_crs", ""))


def _soil_horizon_lookup(dialog):
    """Categorical soil horizons, one class layer each, through the one lookup table."""
    _, _, table, _, mapping, class_field = _widgets(dialog, _LOOKUP_WIDGETS["soil"])
    lookup_table = _combo_path(table)
    if not lookup_table or not Path(lookup_table).is_file():
        raise ValueError("Select an existing lookup table.")
    if not mapping.currentText().strip() or not class_field.currentText().strip():
        raise ValueError("Choose the lookup table's mapping and class fields.")
    layers = _rows(dialog, "soil_lookup")
    return SingleLayerLookupConfig(layers[0][0], lookup_table, mapping.currentText().strip(),
                                   class_field.currentText().strip(), layers=layers)


def _lai_netcdf(dialog):
    combo, target = dialog.comboBox_laiNetCDFInput, dialog.comboBox_timestepLAIInput.currentText().strip()
    source = _combo_path(combo)
    if not source or Path(source).suffix.lower() != ".nc" or not Path(source).is_file() or not target:
        raise ValueError("Select a NetCDF file and an output temporal resolution.")
    if not _ensure_raster_details(dialog, combo):
        return None
    details = _combo_raster_details(combo)
    return LaiNetcdfConfig(source, target, details.get("variable", ""), details.get("source_crs", ""))


# --- Layer tables (periods, horizons) --------------------------------------

def _multi(dialog, key) -> bool:
    return getattr(dialog, _SWITCH[key]).isChecked()


def _switched(dialog, checkbox):
    """The table's widgets follow the switch; switched off, dates span the simulation."""
    key, labels = _SWITCHES[checkbox]
    on = getattr(dialog, checkbox).isChecked()
    table, _combo, first, second, add = _TABLES[key]
    for name in (table, first, second, add, *labels):
        getattr(dialog, name).setEnabled(on)
    if not on and dialog.project_folder and key.startswith("lc"):
        from .time import TIME_WIDGETS

        for name, time in ((first, "sim_start"), (second, "sim_end")):
            getattr(dialog, name).setDateTime(getattr(dialog, TIME_WIDGETS[time]).dateTime())


def _value(widget) -> str:
    return widget.dateTime().toString("yyyy-MM-ddTHH:mm:ss") if hasattr(widget, "dateTime") else widget.text().strip()


def add_row(dialog, key):
    """The layer and the two values chosen above as a row; the file must be local (it is read directly).
    Categorical soil horizons may be vectors; a GeoPackage keeps its chosen layer."""
    _table, combo, first, second, _add = _widgets(dialog, _TABLES[key])
    adapter = next((item for item in dialog._lookup_adapters.values() if item.combo_box is combo), None)
    path = adapter.source_path() if adapter is not None else _combo_path(combo)
    if not path or not Path(path.split("|", 1)[0]).is_file():
        QMessageBox.warning(dialog, "Input layers", "Select a local file first.")
        return
    dialog._layer_rows[key].append([path, _value(first), _value(second)])
    _show_rows(dialog, key)


def _show_rows(dialog, key):
    table, rows = getattr(dialog, _TABLES[key][0]), dialog._layer_rows[key]
    table.setRowCount(len(rows))
    for index, row in enumerate(rows):
        for column, text in enumerate(row):
            table.setItem(index, column, QTableWidgetItem(str(text)))
        remove = QPushButton("✖", table)
        remove.setStyleSheet("color: #c0392b;")
        remove.clicked.connect(lambda _checked=False, row=row: (rows.remove(row), _show_rows(dialog, key)))
        table.setCellWidget(index, 3, remove)


def _rows(dialog, key) -> tuple:
    """The table's rows as (path, a, b), checked: several, each from a to b (depths as numbers)."""
    rows = dialog._layer_rows[key]
    if len(rows) < 2:
        raise ValueError("Add at least two rows, or uncheck the box to Set one file.")
    if key.startswith("soil"):
        try:
            rows = [[path, float(upper), float(lower)] for path, upper, lower in rows]
        except ValueError as error:
            raise ValueError("Enter the horizon depths in mm as numbers.") from error
    if any(a >= b for _path, a, b in rows):
        raise ValueError("Each row must end after it starts (a period, or an upper above a lower depth).")
    return tuple(tuple(row) for row in rows)


def _land_use_periods(dialog):
    from ...core.handlers.lookup import (
        LandUseInput,
        LandUsePeriod,
        validate_land_use_input,
    )
    from ...core.handlers.state.nml_settings import load_settings
    from ...core.handlers.store.layout import project_version

    _, _, table, _, mapping, class_field = _widgets(dialog, _LOOKUP_WIDGETS["lc"])
    value = LandUseInput(
        tuple(LandUsePeriod(start, end, Path(path)) for path, start, end in _rows(dialog, "lc_lookup")),
        Path(_combo_path(table)), mapping.currentText().strip(), class_field.currentText().strip())
    times = load_settings(dialog.project_folder).get("time", {})
    validate_land_use_input(value, simulation_start=times.get("sim_start", "1900-01-01T00:00:00"),
                            simulation_end=times.get("sim_end", "2101-01-01T00:00:00"),
                            version=project_version(dialog.project_folder))
    return value


# --- Restore --------------------------------------------------------------

def fill(dialog):
    """The saved inputs into the pages' widgets, and each committed type's radio and page."""
    folder = dialog.project_folder
    for kind in KINDS:
        ready = dialog._categorical_ready_configs.get(kind) or {}
        combo, _, definition, _ = _widgets(dialog, _READY_WIDGETS[kind])
        _populate_file_combo(combo, folder, _SOURCES[kind], ready.get("input_path", ""))
        _restore_raster_details(combo, ready)
        if definition is not None:
            _populate_file_combo(definition, folder, "lookup", ready.get("classdefinition_path", ""))
        saved = dialog._categorical_lookup_configs.get(kind) or (
            land_use_input_value(dialog._advanced_inputs["land_cover"]).as_dict()
            if kind == "lc" and "land_cover" in dialog._advanced_inputs else {})
        adapter = dialog._lookup_adapters[kind]
        _populate_layer_combo(adapter, folder, _SOURCES[kind], saved.get("input_path", ""))
        _restore_raster_details(adapter.combo_box, saved)
        _, _, table, _, mapping, class_field = _widgets(dialog, _LOOKUP_WIDGETS[kind])
        _populate_file_combo(table, folder, "lookup", str(saved.get("lookup_table", "") or ""))
        _refresh_fields(table, mapping, class_field, (saved.get("mapping_field", ""), saved.get("class_field", "")))
        text = categorical_input_mode(dialog, kind)
        main.check(dialog, PAGES[kind], text)
        show_type(dialog, kind, text)
    _populate_file_combo(dialog.comboBox_laiNetCDFInput, folder, "lai", dialog._lai_input_config.get("input_path", ""))
    _restore_raster_details(dialog.comboBox_laiNetCDFInput, dialog._lai_input_config)
    timestep = dialog.comboBox_timestepLAIInput.findText(str(dialog._lai_input_config.get("target_timestep", "")))
    if timestep >= 0:
        dialog.comboBox_timestepLAIInput.setCurrentIndex(timestep)
    periods = dialog._advanced_inputs.get("land_cover")
    dialog._layer_rows = {
        "lc_lookup": [[item["file_path"], item["start_time"], item["end_time"]]
                      for item in land_use_input_value(periods).as_dict()["periods"]] if periods else [],
        "lc_ready": [list(row) for row in (dialog._categorical_ready_configs.get("lc") or {}).get("layers", [])],
        "soil_ready": [list(row) for row in (dialog._categorical_ready_configs.get("soil") or {}).get("layers", [])],
        "soil_lookup": [list(row) for row in (dialog._categorical_lookup_configs.get("soil") or {}).get("layers", [])],
    }
    for checkbox, (key, _labels) in _SWITCHES.items():
        getattr(dialog, checkbox).setChecked(bool(dialog._layer_rows[key]))
        _switched(dialog, checkbox)
        _show_rows(dialog, key)
    soil = dialog._advanced_inputs.get("soil")
    dialog._soil_horizons.set_input(soil if soil else {"horizons": []})


def _configure_ready(dialog, kind, config):
    try:
        require_common_grid(dialog)
    except ValueError as error:
        QMessageBox.warning(dialog, "Common Grid Required", str(error))
        return False
    if kind == "lai" and ready_layers(config.input_path, getattr(config, "variable", "")) > 1:
        # A LAI series, not the class grid: the NetCDF File input takes it (with its time step).
        dialog._lai_input_config = {"input_path": config.input_path, "variable": config.variable,
                                    "source_crs": getattr(config, "source_crs", "")}
        _populate_file_combo(dialog.comboBox_laiNetCDFInput, dialog.project_folder, "lai", config.input_path)
        _restore_raster_details(dialog.comboBox_laiNetCDFInput, dialog._lai_input_config)
        main.check(dialog, "lai", "NetCDF File")
        show_type(dialog, "lai", "NetCDF File")
        QMessageBox.information(dialog, "LAI input", "This file is a LAI series: choose its time step "
                                "under NetCDF File and press Set.")
        return False
    dialog._categorical_ready_configs[kind] = {
        "input_path": config.input_path,
        "classdefinition_path": getattr(config, "classdefinition_path", ""),
        "variable": getattr(config, "variable", ""),
        "source_crs": getattr(config, "source_crs", ""),
        **({"layers": [list(row) for row in config.layers]} if getattr(config, "layers", ()) else {}),
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


def _configure_lookup(dialog, kind, config):
    dialog._categorical_lookup_configs[kind] = {
        "input_path": config.input_path,
        "lookup_table": config.lookup_table,
        "mapping_field": config.mapping_field,
        "class_field": config.class_field,
        "variable": getattr(config, "variable", ""),
        "source_crs": getattr(config, "source_crs", ""),
        **({"layers": [list(row) for row in config.layers]} if getattr(config, "layers", ()) else {}),
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


def _configure_lai_netcdf(dialog, _kind, config):
    dialog._lai_input_config = {
        "input_path": config.input_path,
        "target_timestep": config.target_timestep,
        "variable": getattr(config, "variable", ""),
        "source_crs": getattr(config, "source_crs", ""),
    }
    dialog._categorical_lookup_configs.pop("lai", None)
    save_lai_nml_input(dialog.project_folder, dialog._lai_input_config)
    return True


def save_lai_nml_input(project_folder, config):
    """The LAI NetCDF handoff from the dialog's choice: comboBox_timestepLAIInput's
    text becomes `time_step` (mHM `lai_time_step`). Execute All writes it again from
    the saved choice, so a lost section comes back."""
    from ...externals.mhm_tools_handler import lai_time_step

    target = config.get("target_timestep") or "Long Term Mean Monthly Gridded Data"
    update_section(project_folder, "lai", {
        "mode": "netcdf",
        "source_path": config.get("input_path", ""),
        "source_variable": config.get("variable", ""),
        "target_timestep": target,
        "time_step": lai_time_step(target),
        "output_path": relative_workspace_path(
            project_folder, os.path.join(lai_folder(project_folder), "lai.nc")
        ),
        "variable": "lai",
    })


def _configure_historical_land_use(dialog, _kind, value):
    dialog._advanced_inputs["land_cover"] = value
    dialog._land_cover_ready_source = ""
    save_land_cover_nml_input(dialog, value)
    return True


def _configure_multi_horizon_soil(dialog, _kind, value):
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
        "mhm_ready" if is_ready(categorical_input_mode(dialog, "soil"))
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
