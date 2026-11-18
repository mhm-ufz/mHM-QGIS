"""Widget wiring for the main form (`mhm_qgis_main.ui`).

`bind(dialog)` is called once from `MhmQgisDialog.__init__`. Each processing
button names its controller or workflow entry point at the connection site.
"""
from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtWidgets import QMessageBox, QToolButton

from .. import project_tree, tooltip
from ..controllers import (
    categorical,
    forcing,
    grid,
    main,
    model,
    multi_domain,
    post_context,
    process_cases,
    project,
    time,
    version,
)
from ..dialogs.input_selection import _browse_raster_into_combo


def _bind_input_sources(dialog):
    """Connect browsing and categorical type controls."""
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

    # Land use, soil, geology and LAI: radios → their stacked page, Set → categorical.set_input
    categorical.bind(dialog)
    # Gauge Point/Raster radios → their page; the raster browse → its combo
    main.radios(dialog, "gauges", lambda _text: main.show_gauge_type(dialog))
    dialog.pushButton_browsegaugeRasterInput.clicked.connect(lambda: _browse_raster_into_combo(
        dialog, dialog.comboBox_gaugeRaterInput, "Select gauge-ID raster",
        "Raster files (*.asc *.nc *.tif *.tiff);;All files (*)"))
    main.radios(dialog, "domainDefinition", lambda text: main.handle_domain_definition_type(dialog, text))
    # toolButton_*Info → its plain-text toolTip in a message box, as in nml-tools
    for info in dialog.findChildren(QToolButton):
        if info.objectName().endswith("Info"):
            info.clicked.connect(lambda _checked=False, info=info: QMessageBox.information(
                dialog, info.accessibleName(), info.toolTip()))

    # A bound slot, so the connection dies with the dialog on plugin reload.
    qgis_project = QgsProject.instance()
    qgis_project.layersAdded.connect(dialog.refresh_input_sources)
    qgis_project.layersRemoved.connect(dialog.refresh_input_sources)


def bind(dialog):
    """Connect all UI element signals to appropriate slots."""

    # Closure Help → tooltip.start (a guided tour of the visible page)
    dialog.Closure.helpRequested.connect(lambda: tooltip.start(dialog))

    # treeWidget_projectStructure → project_tree.select; folder and version → project_tree.refresh
    dialog.treeWidget_projectStructure.currentItemChanged.connect(lambda row, _old: project_tree.select(dialog, row))
    dialog.treeWidget_projectStructure.itemClicked.connect(lambda row, column: project_tree.clicked(dialog, row, column))
    dialog.lineEdit_ProjectFolder.textChanged.connect(lambda _text: project_tree.refresh(dialog))
    dialog.comboBox_mHMversion.currentIndexChanged.connect(lambda _index: project_tree.refresh(dialog))
    dialog.comboBox_mHMversion.currentIndexChanged.connect(lambda _index: process_cases.inputs(dialog))
    # pushButton_refPageDem → DEM, whose grid the derivatives are derived from or replaced on
    dialog.pushButton_refPageDem.clicked.connect(lambda: project_tree.go_to(dialog, "dem"))
    # checkBox_isDerivedFromDemGrid unticked → the Replace box (its raster: ReplaceBox); Set → grid.set_derivative
    dialog.checkBox_isDerivedFromDemGrid.toggled.connect(
        lambda derived: dialog.groupBox_replaceDemDerivativesVar.setEnabled(not derived))
    dialog.pushButton_setDemDerivativesVar.clicked.connect(lambda: grid.set_derivative(dialog))

    # Project management
    # pushButton_BrowseProjectFolder → project.browse_folder; pushButton_setProject → project.set_folder
    dialog.pushButton_BrowseProjectFolder.clicked.connect(lambda: project.browse_folder(dialog))
    dialog.pushButton_setProject.clicked.connect(lambda: project.set_folder(dialog))
    # pushButton_createOrLoadDomainStructure → project.create_structure
    dialog.pushButton_createOrLoadDomainStructure.clicked.connect(
        lambda: project.create_structure(dialog)
    )
    dialog.comboBox_domainFolderSelector.currentIndexChanged.connect(
        lambda: dialog.comboBox_mHMversion.setEnabled(
            dialog.comboBox_domainFolderSelector.currentData() is None
        )
    )
    # Multi-domain setup
    dialog.comboBox_multiDomainFolderSelector.currentIndexChanged.connect(
        lambda: dialog.comboBox_mHMversion.setEnabled(
            dialog.comboBox_multiDomainFolderSelector.currentData() is None
        )
    )
    # pushButton_createorLoadMultiDomainStructure → multi_domain.create_or_load
    dialog.pushButton_createorLoadMultiDomainStructure.clicked.connect(
        lambda: multi_domain.create_or_load(dialog)
    )
    # pushButton_browseDomainFolder → multi_domain.browse_domain
    dialog.pushButton_browseDomainFolder.clicked.connect(lambda: multi_domain.browse_domain(dialog))
    # pushButton_addDomainFolder → multi_domain.add_domain
    dialog.pushButton_addDomainFolder.clicked.connect(lambda: multi_domain.add_domain(dialog))
    # pushButton_prepareMultiDomainSetup → multi_domain.prepare
    dialog.pushButton_prepareMultiDomainSetup.clicked.connect(lambda: multi_domain.prepare(dialog))
    dialog.comboBox_mhmWorkType.currentIndexChanged.connect(
        lambda: project.work_type_changed(dialog)
    )
    # pushButton_addMeteoInput → forcing.edit_meteo_input
    dialog.pushButton_addMeteoInput.clicked.connect(lambda: forcing.edit_meteo_input(dialog))
    # pushButton_addObservationVar → forcing.edit_observation_input
    dialog.pushButton_addObservationVar.clicked.connect(
        lambda: forcing.edit_observation_input(dialog))
    # pushButton_setTimePeriod → time.set_period; pushButton_dialogTimePeriodNetcdfFile → time.open_window_dialog
    dialog.pushButton_setTimePeriod.clicked.connect(lambda: time.set_period(dialog))
    dialog.pushButton_dialogTimePeriodNetcdfFile.clicked.connect(lambda: time.open_window_dialog(dialog))
    dialog.spinBox_warmingDays.setMaximum(36600)  # Designer's 99 is shorter than a usual warm-up
    dialog.spinBox_warmingDays.valueChanged.connect(lambda _days: time.warming_changed(dialog))
    dialog.dateTimeEdit_simulationStartTimeInput.dateTimeChanged.connect(lambda _when: time.warming_changed(dialog))
    dialog.dateTimeEdit_evaluationStartTimeInput.dateTimeChanged.connect(lambda _when: time.evaluation_changed(dialog))
    # pushButton_setGauges → main.set_gauges: the drainage network and flow accumulation
    dialog.pushButton_setGauges.clicked.connect(lambda: main.set_gauges(dialog))
    dialog.stackedWidget_project.currentChanged.connect(
        lambda index: main.on_page_changed(dialog, dialog.stackedWidget_project.widget(index))
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
    dialog.checkBox_gaugeOption.toggled.connect(lambda _checked: main.handle_model_input_changed(dialog))
    # Outputs tab: the post-processing context bar, pages and time bar
    post_context.bind(dialog)

    _bind_grid(dialog)

    # Configuration/execution processing
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
    dialog.comboBox_runName.activated.connect(
        lambda index: model.select_run_name(dialog, index)
    )
    # pushButton_execute_mHM → model.run
    dialog.pushButton_execute_mHM.clicked.connect(lambda: model.run(dialog))
    # checkBox_enableParameterArgument → its -p widgets; pushButton_browseParameterNamelistFile → model.browse_parameter_namelist
    for widget in (dialog.label_parameterArgument, dialog.lineEdit_pathParameterArgument,
                   dialog.pushButton_browseParameterNamelistFile):
        dialog.checkBox_enableParameterArgument.toggled.connect(widget.setEnabled)
    dialog.pushButton_browseParameterNamelistFile.clicked.connect(lambda: model.browse_parameter_namelist(dialog))
    # pushButton_mapCanvas → dialog.open_map_canvas
    dialog.pushButton_mapCanvas.clicked.connect(dialog.open_map_canvas)
    # pushButton_terminal → dialog.open_project_terminal
    dialog.pushButton_terminal.clicked.connect(dialog.open_project_terminal)
    # pushButton_log → dialog.open_log
    dialog.pushButton_log.clicked.connect(dialog.open_log)
    # pushButton_pythonConsole → dialog.open_python_console; QGIS Desktop only
    dialog.pushButton_pythonConsole.clicked.connect(dialog.open_python_console)
    if dialog.iface is None:
        dialog.pushButton_pythonConsole.setEnabled(False)
        dialog.pushButton_pythonConsole.setToolTip("Available in QGIS Desktop")
    dialog.comboBox_mHMversion.currentIndexChanged.connect(
        lambda index=None: model.version_changed(dialog)
    )
    _bind_input_state(dialog)


def _bind_grid(dialog):
    """The Resolution page and the DEM page's Set."""
    # pushButton_browseL{0,1,11,2}File → grid.browse_file
    for level in grid.LEVELS:
        getattr(dialog, f"pushButton_browse{level}File").clicked.connect(
            lambda _checked=False, level=level: grid.browse_file(dialog, level))
        getattr(dialog, f"checkBox_extent{level}enforce").toggled.connect(lambda _on: grid.changed(dialog))
        # label_display{level}File / GridCommon eyes → grid.toggle_display (the file, the Set grid)
        for label in (f"label_display{level}File", f"label_display{level}GridCommon"):
            getattr(dialog, label).linkActivated.connect(lambda key: grid.toggle_display(dialog, key))
        for name, _key in (*grid.CORNERS, ("cellSize", "cellsize")):
            getattr(dialog, f"label_{name}{level}Common").linkActivated.connect(
                lambda href: grid.explain(dialog, href))
        # toolButton_description{level}File → its toolTip (the file's steps) in a message box
        button = getattr(dialog, f"toolButton_description{level}File")
        button.clicked.connect(lambda _checked=False, level=level, button=button: QMessageBox.information(
            dialog, f"{level} file", button.toolTip()))
    dialog.lineEdit_L0Resolution.editingFinished.connect(lambda: grid.snap_l0(dialog))
    for level in ("L1", "L11", "L2"):
        getattr(dialog, f"lineEdit_{level}Resolution").textChanged.connect(
            lambda _text, level=level: grid.sync_multiplier(dialog, level))
        getattr(dialog, f"lineEdit_{level}Resolution").editingFinished.connect(
            lambda level=level: (grid.sync_multiplier(dialog, level, snap=True), grid.changed(dialog)))
        getattr(dialog, f"spinBox_{level}ResolutionMultiplier").valueChanged.connect(
            lambda n, level=level: (grid.multiplier_changed(dialog, level, n), grid.changed(dialog)))
    # pushButton_setLatLon → grid.set_resolution
    dialog.pushButton_setLatLon.clicked.connect(lambda: grid.set_resolution(dialog))
    # pushButton_setDEM → grid.set_dem; a new DEM is checked against the common L0 grid at once
    dialog.pushButton_setDEM.clicked.connect(lambda: grid.set_dem(dialog))
    dialog.input_combo("dem").layerChanged.connect(lambda _layer=None: grid.check_dem(dialog))
    # pushButton_setCRS → grid.set_crs; the CRS widget alone applies nothing
    dialog.pushButton_setCRS.clicked.connect(lambda: grid.set_crs(dialog))


def _bind_input_state(dialog):
    """Save input selections when editable inputs change."""
    changed = lambda *_args: main.handle_model_input_changed(dialog)  # noqa: E731
    for _, combo_box in main.input_layer_widgets(dialog):
        combo_box.layerChanged.connect(changed)
