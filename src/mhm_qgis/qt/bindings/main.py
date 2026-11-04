"""Widget wiring for the main form (`mhm_qgis_main.ui`).

`bind(dialog)` is called once from `MhmQgisDialog.__init__`. Each processing
button names its controller or workflow entry point at the connection site.
"""
from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtWidgets import QMessageBox, QToolButton

from ...qgis_bridge.display import grid as grid_display
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
    version,
)
from ..workflows import observations


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

    # Land use, soil, geology and LAI radios → categorical.show; domain radios → main.handle_domain_definition_type
    categorical.bind(dialog)
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
    # pushButton_refL0/L2 → the L0/L2 Reference rows the Resolution page's values come from;
    # pushButton_refL0Derivatives → L0 Reference, the grid of the DEM derivatives
    for button in (dialog.pushButton_refL0, dialog.pushButton_refL0Derivatives):
        button.clicked.connect(lambda: project_tree.go_to(dialog, "l0_reference"))
    dialog.pushButton_refL2.clicked.connect(lambda: project_tree.go_to(dialog, "l2_reference"))

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
    # pushButton_setPrecipitationInput → the L2 multiplier and the simulation period
    dialog.pushButton_setPrecipitationInput.clicked.connect(lambda: grid.set_precipitation_input(dialog))
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

    # pushButton_browseDemOverride / pushButton_demOverride → main.browse_dem_override / override_dem_derivative
    dialog.pushButton_browseDemOverride.clicked.connect(lambda: main.browse_dem_override(dialog))
    dialog.pushButton_demOverride.clicked.connect(lambda: main.override_dem_derivative(dialog))
    dialog.tableWidget_demDerivatives.cellClicked.connect(lambda row, _column: main.show_dem_derivatives(
        dialog, dialog.tableWidget_demDerivatives.item(row, 0).text()))

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
    dialog.comboBox_mHMversion.currentIndexChanged.connect(
        lambda index=None: model.version_changed(dialog)
    )
    _bind_input_state(dialog)


def _bind_grid(dialog):
    """Refresh or invalidate the common grid when one of its inputs changes."""
    dem = dialog.input_combo("dem")
    dem.layerChanged.connect(lambda layer=None: grid.update_l0_resolution_from_dem(dialog, layer))
    dem.layerChanged.connect(lambda layer=None: grid.invalidate_grid(dialog))
    # pushButton_setCRS → grid.set_crs; the CRS widget alone applies nothing
    dialog.pushButton_setCRS.clicked.connect(lambda: grid.set_crs(dialog))
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
            grid.sync_multiplier(dialog, "L1"),
            grid.update_l1_resolution_label(dialog),
            grid.update_l11_resolution_label(dialog),
        )
    )
    dialog.lineEdit_L11Resolution.textChanged.connect(
        lambda _text: (grid.sync_multiplier(dialog, "L11"), grid.update_l11_resolution_label(dialog))
    )
    # pushButton_createLatLon → grid.process_lat_lon
    dialog.pushButton_createLatLon.clicked.connect(lambda: grid.process_lat_lon(dialog))


def _bind_input_state(dialog):
    """Save input selections when editable inputs change."""
    changed = lambda *_args: main.handle_model_input_changed(dialog)  # noqa: E731
    for _, combo_box in main.input_layer_widgets(dialog):
        combo_box.layerChanged.connect(changed)
    for level in ("L1", "L11"):
        getattr(dialog, f"lineEdit_{level}Resolution").editingFinished.connect(
            lambda level=level: (grid.sync_multiplier(dialog, level, snap=True), changed()))
        getattr(dialog, f"spinBox_{level}ResolutionMultiplier").valueChanged.connect(
            lambda n, level=level: (grid.multiplier_changed(dialog, level, n), changed()))
