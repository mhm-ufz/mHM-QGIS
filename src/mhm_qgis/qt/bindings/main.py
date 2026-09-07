# -*- coding: utf-8 -*-
"""Widget wiring for the main form (`mhm_qgis_main.ui`).

`bind(dialog)` is called once from `MhmQgisDialog.__init__`. The
`connect_processor_button` / `connect_optional_processor_button` helpers stay on
the dialog: they reach its logger and task coordinator, so they are behaviour
rather than wiring.
"""
from __future__ import annotations

try:
    from qgis.core import QgsProject
except ImportError:
    from ...standalone import install

    install(force=True)
    from qgis.core import QgsProject

from ...qgis_bridge.display import meteo as meteo_display
from ...qgis_bridge.display import output as output_display
from ..controllers import elevation_band, main


def _bind_input_sources(dialog):
    """Connect folder searching, browsing, and categorical type controls."""
    dialog.checkBox_enableFolderSearch.toggled.connect(
        lambda checked=False: (
            dialog.refresh_input_sources(),
            dialog.save_input_state(),
        )
    )
    for kind, button_name in (
        ("dem", "pushButton_browseDEMInput"),
        ("pour_points", "pushButton_browsePourPointInput"),
        ("land_cover", "pushButton_browseLandUseInput"),
        ("soil", "pushButton_soilInput"),
        ("geology", "pushButton_geologyInput"),
        ("lai", "pushButton_laiInput"),
    ):
        button = getattr(dialog, button_name, None)
        if button is not None:
            button.setToolTip(
                f"Browse for {kind.replace('_', ' ')} input file"
            )
            button.clicked.connect(
                lambda checked=False, input_kind=kind: dialog.browse_input_file(
                    input_kind
                )
            )

    for kind, combo_name in (
        ("lc", "comboBox_landUseInputType"),
        ("soil", "comboBox_soil_inputType"),
        ("geology", "comboBox_geology_inputType"),
        ("lai", "comboBox_lai_inputType"),
    ):
        combo = getattr(dialog, combo_name, None)
        if combo is not None:
            combo.activated.connect(
                lambda _index, input_kind=kind, widget=combo: (
                    dialog.handle_categorical_type(
                        input_kind, widget.currentText()
                    )
                )
            )

    project = QgsProject.instance()
    for signal_name in ("layersAdded", "layersRemoved"):
        signal = getattr(project, signal_name, None)
        if signal is not None:
            try:
                signal.connect(lambda *_args: dialog.refresh_input_sources())
            except Exception:
                pass

def bind(dialog):
    """Connect all UI element signals to appropriate slots."""

    # Project management
    dialog.pushButton_BrowseProjectFolder.clicked.connect(dialog.select_project_folder)
    dialog.tabWidget_steps.currentChanged.connect(dialog.on_tab_changed)
    _bind_input_sources(dialog)
    dialog.pushButton_threads.clicked.connect(dialog.open_thread_display)
    dialog.pushButton_observationDisplay_discharge.clicked.connect(
        lambda checked=False: main.display_observation_discharge(dialog)
    )

    # Morphology/Geometry processing is dispatched by the Qt task API. The
    # callback is intentionally empty for background actions: the dispatcher
    # selects the API command by its stable action name.
    dialog.connect_optional_processor_button(
        "pushButton_fillDem", "Fill DEM", lambda: None
    )
    dialog.connect_optional_processor_button(
        "pushButton_createNetwork",
        "Create Channel Network",
        lambda: None,
    )
    dialog.connect_optional_processor_button(
        "pushButton_snapPoints",
        "Snap Pour Points",
        lambda: None,
    )
    dialog.pushButton_delineate.clicked.connect(dialog.open_domain_delineator)
    dialog.connect_processor_button(
        dialog.pushButton_elevation_bands,
        "Elevation Bands",
        lambda: elevation_band.process_elevation_bands(dialog),
    )
    dialog.connect_processor_button(
        dialog.pushButton_bandDetails,
        "Elevation Band Land Cover Details",
        lambda: elevation_band.process_band_details(dialog),
    )

    # Hydrological processing
    dialog.connect_optional_processor_button(
        "pushButton_aspect", "Aspect", lambda: None
    )
    dialog.connect_optional_processor_button(
        "pushButton_slope", "Slope", lambda: None
    )
    dialog.connect_optional_processor_button(
        "pushButton_flowAccumulation",
        "Flow Accumulation",
        lambda: None,
    )
    dialog.connect_optional_processor_button(
        "pushButton_flowDirection",
        "Flow Direction",
        lambda: None,
    )
    dialog.connect_optional_processor_button(
        "pushButton_gaugePosition",
        "Gauge Position",
        lambda: None,
    )
    try:
        dialog.input_combo("pour_points").layerChanged.connect(
            dialog.populate_pour_point_outlet_fields
        )
        dialog.input_combo("pour_points").layerChanged.connect(
            lambda layer=None: dialog.update_gauged_outlet_count(
                layer
            )
        )
    except Exception:
        pass
    dialog.update_gauged_outlet_count()

    dialog.comboBox_pourPointOutletID.currentIndexChanged.connect(
        dialog.handle_model_input_changed
    )
    dialog.checkBox_DEMdomain.toggled.connect(
        dialog.handle_model_input_changed
    )
    dialog.comboBox_domainDefinitionType.activated.connect(
        dialog.handle_domain_definition_type
    )

    # Layer processing
    dialog.connect_optional_processor_button(
        "pushButton_landUse",
        "Land Use",
        lambda: None,
    )
    dialog.connect_optional_processor_button(
        "pushButton_soil", "Soil", lambda: None
    )
    dialog.connect_optional_processor_button(
        "pushButton_hydrogeology",
        "Hydrogeology",
        lambda: None,
    )
    if hasattr(dialog, "pushButton_LAI"):
        dialog.connect_processor_button(
            dialog.pushButton_LAI,
            "LAI",
            lambda: None,
            background=True,
        )
    dialog.pushButton_morphLayerDisplay.clicked.connect(
        lambda checked=False: dialog.display_selected_morphology_layer()
    )
    dialog.comboBox_morphVariableToDisplay.currentIndexChanged.connect(
        lambda index=None: dialog.refresh_morphology_date_control()
    )

    # Meteorology display: the slider and the date editor drive one step index.
    dialog.pushButton_meteoVarDisplay.clicked.connect(
        lambda checked=False: meteo_display.display_selected_layer(dialog)
    )
    dialog.horizontalSlider_meteoTimeSelector.valueChanged.connect(
        lambda value: meteo_display.slider_moved(dialog, value)
    )
    dialog.dateTimeEdit_meteoVarDisplay.dateChanged.connect(
        lambda value=None: meteo_display.date_changed(dialog, value)
    )

    # Model output display: same three controls over an irregular step list.
    dialog.pushButton_outputVarDisplay.clicked.connect(
        lambda checked=False: output_display.display_selected_layer(dialog)
    )
    dialog.horizontalSlider_ouputTimeSelector.valueChanged.connect(
        lambda value: output_display.slider_moved(dialog, value)
    )
    dialog.dateTimeEdit_outputVarDisplay.dateTimeChanged.connect(
        lambda value=None: output_display.date_changed(dialog, value)
    )

    # Reset geometry
    dialog.pushButton_resetGeometry.clicked.connect(dialog.reset_geometry_processing)

    # Batch morphology workflows
    execute_all_button = dialog.morphology_workflow_button("execute_all")
    if execute_all_button is not None:
        execute_all_button.clicked.connect(dialog.start_execute_all_processing)
    dialog.pushButton_executeMeteoMorphSetup.clicked.connect(
        dialog.start_meteo_morph_setup_processing
    )

    # LAI file browser
    if hasattr(dialog, "pushButton_browse_lai"):
        dialog.pushButton_browse_lai.clicked.connect(dialog.browse_lai_file)

    _bind_grid_resolution(dialog)

    for kind, button in (
        ("precipitation", dialog.pushButton_browsePrecipitationFile),
        ("temperature", dialog.pushButton_browseTemperatureFile),
        ("pet", dialog.pushButton_browsePetFile),
    ):
        button.clicked.connect(
            lambda checked=False, meteo_kind=kind:
            dialog.browse_meteo_input_folder(meteo_kind)
        )
    for kind, folder_combo, source_combo in dialog.meteo_input_widgets():
        folder_combo.currentIndexChanged.connect(
            lambda index=None, meteo_kind=kind:
            dialog.handle_meteo_input_changed(meteo_kind)
        )
        source_combo.currentIndexChanged.connect(
            lambda index=None, meteo_kind=kind:
            dialog.handle_meteo_input_changed(meteo_kind)
        )

    # Configuration/execution processing
    dialog.connect_processor_button(
        dialog.pushButton_createLatLon,
        "Create LatLon",
        dialog.process_lat_lon,
        background=True,
    )
    dialog.update_latlon_button_state()
    dialog.pushButton_edit_nmls.setToolTip("Edit mHM namelists")
    dialog.connect_processor_button(
        dialog.pushButton_edit_nmls,
        "Edit Namelists",
        dialog.edit_namelists,
    )
    run_mhm_button = dialog.pushButton_execute_mHM
    if run_mhm_button is not None:
        run_mhm_button.setToolTip("Run mHM")
        dialog.connect_processor_button(
            run_mhm_button,
            "Run mHM",
            dialog.run_mhm,
        )
    terminal_button = dialog.pushButton_terminal
    if terminal_button is not None:
        terminal_button.clicked.connect(dialog.open_project_terminal)
    dialog.comboBox_mHMversion.currentIndexChanged.connect(
        lambda index=None: dialog.handle_version_changed()
    )
    _bind_input_state(dialog)
    dialog.connect_input_state_teardown_guards()

    # Initialize CRS widget with project CRS
    project_crs = QgsProject.instance().crs()
    if project_crs.isValid():
        dialog.mProjectionSelectionWidget_crs.setCrs(project_crs)


def _bind_grid_resolution(dialog):
    """Refresh derived grid controls when DEM/L1/L11 selections change."""
    try:
        dialog.input_combo("dem").layerChanged.connect(
            lambda layer=None: dialog.update_l0_resolution_from_dem(layer)
        )
        dialog.input_combo("dem").layerChanged.connect(
            lambda layer=None:
            dialog.inspect_meteo_selection("precipitation", show_errors=False)
        )
    except Exception:
        pass

    dialog.spinBox_L2ResolutionMultiplier.valueChanged.connect(
        dialog.handle_l2_multiplier_changed
    )
    try:
        dialog.mProjectionSelectionWidget_crs.crsChanged.connect(
            lambda crs=None:
            dialog.inspect_meteo_selection("precipitation", show_errors=False)
        )
    except Exception:
        pass

    try:
        dialog.comboBox_L1.currentIndexChanged.connect(
            lambda index=None: dialog.handle_l1_resolution_changed()
        )
    except Exception:
        pass

    try:
        dialog.comboBox_L11.currentIndexChanged.connect(
            lambda index=None: dialog.update_l11_resolution_label()
        )
    except Exception:
        pass


def _bind_input_state(dialog):
    """Save input selections when editable inputs change."""

    for _, combo_box in dialog.input_layer_widgets():
        try:
            combo_box.layerChanged.connect(
                dialog.handle_model_input_changed
            )
        except Exception:
            pass

    for _, line_edit in dialog.input_text_widgets():
        try:
            line_edit.editingFinished.connect(dialog.handle_model_input_changed)
        except Exception:
            try:
                line_edit.textChanged.connect(
                    dialog.handle_model_input_changed
                )
            except Exception:
                pass

    try:
        dialog.comboBox_lai_inputType.currentIndexChanged.connect(
            dialog.handle_model_input_changed
        )
    except Exception:
        pass

    for combo_box in (
            dialog.comboBox_L1,
            dialog.comboBox_L11):
        if combo_box is None:
            continue
        try:
            combo_box.currentIndexChanged.connect(
                dialog.handle_model_input_changed
            )
        except Exception:
            pass

    try:
        dialog.mProjectionSelectionWidget_crs.crsChanged.connect(
            dialog.handle_model_input_changed
        )
    except Exception:
        pass
