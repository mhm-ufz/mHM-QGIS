"""Widget wiring for the main form (`mhm_qgis_main.ui`).

`bind(dialog)` is called once from `MhmQgisDialog.__init__`. Each processing
button names its controller or workflow entry point at the connection site.
"""
from __future__ import annotations

from qgis.core import QgsProject

from ...qgis_bridge.display import grid as grid_display
from ...qgis_bridge.display import meteo as meteo_display
from ...qgis_bridge.display import output as output_display
from ..controllers import (
    categorical,
    forcing,
    grid,
    main,
    model,
    project,
    time,
    version,
)
from ..workflows import meteorology, observations


def _bind_input_sources(dialog):
    """Connect folder searching, browsing, and categorical type controls."""
    dialog.checkBox_enableFolderSearch.toggled.connect(
        lambda checked=False: (
            dialog.refresh_input_sources(),
            dialog.save_input_state(),
        )
    )
    for kind, button in (
        ("dem", dialog.pushButton_browseDEMInput),
        ("pour_points", dialog.pushButton_browsePourPointInput),
    ):
        button.setToolTip(f"Browse for {kind.replace('_', ' ')} input file")
        # Input browse button → main.browse_input_file
        button.clicked.connect(
            lambda checked=False, input_kind=kind: main.browse_input_file(
                dialog, input_kind
            )
        )

    for kind in categorical.KINDS:
        combo = categorical.categorical_type_combo(dialog, kind)
        combo.activated.connect(
            lambda _index, input_kind=kind, widget=combo: (
                categorical.handle_categorical_type(
                    dialog, input_kind, widget.currentText()
                )
            )
        )

    # A bound slot, so the connection dies with the dialog on plugin reload.
    qgis_project = QgsProject.instance()
    qgis_project.layersAdded.connect(dialog.refresh_input_sources)
    qgis_project.layersRemoved.connect(dialog.refresh_input_sources)


def bind(dialog):
    """Connect all UI element signals to appropriate slots."""

    # Project management
    # pushButton_BrowseProjectFolder → project.select_folder
    dialog.pushButton_BrowseProjectFolder.clicked.connect(
        lambda: project.select_folder(dialog)
    )
    # pushButton_createOrLoadDomainStructure → project.create_structure
    dialog.pushButton_createOrLoadDomainStructure.clicked.connect(
        lambda: project.create_structure(dialog)
    )
    dialog.comboBox_domainFolderSelector.currentIndexChanged.connect(
        lambda: dialog.comboBox_mHMversion.setEnabled(
            dialog.comboBox_domainFolderSelector.currentData() is None
        )
    )
    dialog.comboBox_mhmWorkType.currentIndexChanged.connect(
        lambda: project.work_type_changed(dialog)
    )
    # pushButton_scanPrecipitationFiles → forcing.scan_precipitation
    dialog.pushButton_scanPrecipitationFiles.clicked.connect(lambda: forcing.scan_precipitation(dialog))
    # pushButton_addMeteoInput → forcing.edit_meteo_input
    dialog.pushButton_addMeteoInput.clicked.connect(lambda: forcing.edit_meteo_input(dialog))
    # pushButton_addObservationVar → forcing.edit_observation_input
    dialog.pushButton_addObservationVar.clicked.connect(
        lambda: forcing.edit_observation_input(dialog))
    # pushButton_executeAllObservation → observations.start
    dialog.pushButton_executeAllObservation.clicked.connect(
        lambda: observations.start(dialog))
    for name in ("simulationStart", "evaluationStart", "simulationEnd"):
        getattr(dialog, f"dateTimeEdit_{name}TimeInput").dateTimeChanged.connect(
            lambda: time.save(dialog))
    dialog.tabWidget_steps.currentChanged.connect(
        lambda index: main.on_tab_changed(dialog, index)
    )
    _bind_input_sources(dialog)
    # pushButton_threads → dialog.open_thread_display
    dialog.pushButton_threads.clicked.connect(dialog.open_thread_display)
    # pushButton_observationDisplay_discharge → main.display_observation_discharge
    dialog.pushButton_observationDisplay_discharge.clicked.connect(
        lambda checked=False: main.display_observation_discharge(dialog)
    )

    pour_points = dialog.input_combo("pour_points")
    pour_points.layerChanged.connect(
        lambda layer=None: main.populate_pour_point_outlet_fields(dialog, layer)
    )
    pour_points.layerChanged.connect(dialog.update_gauged_outlet_count)
    dialog.comboBox_pourPointOutletID.currentIndexChanged.connect(
        lambda _index: main.handle_model_input_changed(dialog)
    )
    dialog.comboBox_domainDefinitionType.activated.connect(
        lambda index: main.handle_domain_definition_type(dialog, index)
    )

    # pushButton_morphLayerDisplay → main.display_selected_morphology_layer
    dialog.pushButton_morphLayerDisplay.clicked.connect(
        lambda checked=False: main.display_selected_morphology_layer(dialog)
    )
    dialog.comboBox_morphVariableToDisplay.currentIndexChanged.connect(
        lambda index=None: main.refresh_morphology_date_control(dialog)
    )

    # Meteorology display: the slider and the date editor drive one step index.
    dialog.comboBox_meteoVarDisplay.currentIndexChanged.connect(
        lambda index: meteo_display.refresh(dialog)
    )
    # pushButton_meteoVarDisplay → meteo_display.display_selected_layer
    dialog.pushButton_meteoVarDisplay.clicked.connect(
        lambda checked=False: meteo_display.display_selected_layer(dialog)
    )
    dialog.horizontalSlider_meteoTimeSelector.valueChanged.connect(
        lambda value: meteo_display.slider_moved(dialog, value)
    )
    dialog.dateTimeEdit_meteoVarDisplay.dateTimeChanged.connect(
        lambda value=None: meteo_display.date_changed(dialog, value)
    )

    # Model output display: same three controls over an irregular step list.
    # pushButton_outputVarDisplay → output_display.display_selected_layer
    dialog.pushButton_outputVarDisplay.clicked.connect(
        lambda checked=False: output_display.display_selected_layer(dialog)
    )
    dialog.horizontalSlider_ouputTimeSelector.valueChanged.connect(
        lambda value: output_display.slider_moved(dialog, value)
    )
    dialog.dateTimeEdit_outputVarDisplay.dateTimeChanged.connect(
        lambda value=None: output_display.date_changed(dialog, value)
    )

    # Batch morphology workflows
    # pushButton_executeAllMorphology → morphology.start_execute_all
    dialog.pushButton_executeAllMorphology.clicked.connect(
        lambda: dialog.morphology_tasks.start_execute_all()
    )
    # pushButton_executeAllMeteo → meteorology.start
    dialog.pushButton_executeAllMeteo.clicked.connect(
        lambda: meteorology.start(dialog)
    )

    _bind_grid(dialog)

    # Configuration/execution processing
    # pushButton_gridDisplay → grid_display.display_selected_grid
    dialog.pushButton_gridDisplay.clicked.connect(
        lambda checked=False: grid_display.display_selected_grid(dialog)
    )
    dialog.pushButton_gridDisplay.setToolTip("Display the selected model grid")
    dialog.pushButton_edit_nmls.setToolTip("Edit mHM namelists")
    # pushButton_edit_nmls → model.edit_namelists
    dialog.pushButton_edit_nmls.clicked.connect(
        lambda: model.edit_namelists(dialog)
    )
    for button, profiles, update in (
        (dialog.pushButton_editNmlsFinal, None, True),
        (dialog.pushButton_editOptimizationOptions, {"main": ["config_optimize"]}, False),
        (dialog.pushButton_editParameterOptions, {"parameter": []}, False),
    ):
        # Namelist option button → model.edit_namelists
        button.clicked.connect(
            lambda checked=False, p=profiles, u=update: model.edit_namelists(
                dialog, p, update_inputs=u
            )
        )
    dialog.groupBox_editCalibrationNamelists.setEnabled(
        version.selected(dialog).startswith("6"))
    dialog.pushButton_execute_mHM.setToolTip("Run mHM")
    # pushButton_execute_mHM → model.run
    dialog.pushButton_execute_mHM.clicked.connect(lambda: model.run(dialog))
    # pushButton_mapCanvas → dialog.open_map_canvas
    dialog.pushButton_mapCanvas.clicked.connect(dialog.open_map_canvas)
    # pushButton_terminal → dialog.open_project_terminal
    dialog.pushButton_terminal.clicked.connect(dialog.open_project_terminal)
    dialog.comboBox_mHMversion.currentIndexChanged.connect(
        lambda index=None: model.version_changed(dialog)
    )
    _bind_input_state(dialog)


def _bind_grid(dialog):
    """Refresh or invalidate the common grid when one of its inputs changes."""
    dem = dialog.input_combo("dem")
    dem.layerChanged.connect(lambda layer=None: grid.update_l0_resolution_from_dem(dialog, layer))
    dem.layerChanged.connect(lambda layer=None: grid.invalidate_grid(dialog))
    dialog.mProjectionSelectionWidget_crs.crsChanged.connect(
        lambda crs=None: grid.invalidate_grid(dialog)
    )
    dialog.spinBox_L2ResolutionMultiplier.valueChanged.connect(
        lambda value: grid.handle_l2_multiplier_changed(dialog, value)
    )
    # pushButton_browsePrecipitationFile → grid.browse_precipitation_folder
    dialog.pushButton_browsePrecipitationFile.clicked.connect(
        lambda: grid.browse_precipitation_folder(dialog)
    )
    dialog.comboBox_precipitationFile.currentIndexChanged.connect(
        lambda: grid.precipitation_changed(dialog)
    )
    dialog.lineEdit_precipitationGlobDefinition.textChanged.connect(
        lambda: grid.precipitation_changed(dialog)
    )
    dialog.lineEdit_L1Resolution.textChanged.connect(
        lambda _text: (
            grid.update_l1_resolution_label(dialog),
            grid.update_l11_resolution_label(dialog),
        )
    )
    dialog.lineEdit_L11Resolution.textChanged.connect(
        lambda _text: grid.update_l11_resolution_label(dialog)
    )
    # pushButton_createLatLon → grid.process_lat_lon
    dialog.pushButton_createLatLon.clicked.connect(lambda: grid.process_lat_lon(dialog))


def _bind_input_state(dialog):
    """Save input selections when editable inputs change."""
    changed = lambda *_args: main.handle_model_input_changed(dialog)  # noqa: E731
    for _, combo_box in main.input_layer_widgets(dialog):
        combo_box.layerChanged.connect(changed)
    dialog.comboBox_lai_inputType.currentIndexChanged.connect(changed)
    dialog.lineEdit_L1Resolution.editingFinished.connect(changed)
    dialog.lineEdit_L11Resolution.editingFinished.connect(changed)
    dialog.mProjectionSelectionWidget_crs.crsChanged.connect(changed)
