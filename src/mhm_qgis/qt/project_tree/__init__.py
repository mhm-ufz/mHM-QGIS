"""The project tree: each row shows its page of `stackedWidget_project` in the dialog's
one tab (`tab_project`), titled after the row.

Rows are made in Designer; `tree.yml` gives each its key, page, status, inputs, display,
action and summary table, matched by title. Columns: ➤ next step | name | status or ▶ | eye or ↺.
"""
from __future__ import annotations

import os
from functools import cache
from pathlib import Path

import yaml
from qgis.PyQt import sip
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import QHeaderView, QPushButton, QTableWidgetItem

COLUMNS = ("Project structure", "", "", "")  # name | status or ▶ | view | next step (shown first)
STATUS_COLUMN, VIEW, NEXT = 1, 2, 3
SYMBOLS = {"empty": ("⚪", "Empty: no inputs added"),
           "added": ("🟡", "Input set: run ▶ to process it"),
           "processed": ("✅", "Processed")}
ICONS = Path(__file__).parents[1] / "ui" / "forms" / "Resources"


@cache
def load() -> dict:
    """`tree.yml` as {(parent title, title): node}, nested rows included."""
    nodes = {}
    pending = [(None, node) for node in yaml.safe_load(
        Path(__file__).with_name("tree.yml").read_text(encoding="utf-8"))["nodes"]]
    while pending:
        parent, node = pending.pop()
        nodes[parent, node["title"]] = node
        pending += [(node["title"], child) for child in node.get("children", [])]
    return nodes


def _node(row) -> dict:
    return load()[row.parent().text(0) if row.parent() else None, row.text(0)]


def _inputs(node) -> list:
    value = node.get("input", [])
    return value if isinstance(value, list) else [value]


def _rows(dialog, top=None):
    tree = dialog.treeWidget_projectStructure
    pending = [top] if top else [tree.topLevelItem(i) for i in reversed(range(tree.topLevelItemCount()))]
    while pending:
        row = pending.pop()
        yield row
        pending += [row.child(i) for i in reversed(range(row.childCount()))]


def _shown(row) -> bool:
    while row is not None:
        if row.isHidden():
            return False
        row = row.parent()
    return True


def _intro(dialog, _node):
    return "processed" if dialog._intro_answers else "empty"


def _project_setup(dialog, _node):
    """Processed once Set opened the folder in the box; added while it is only browsed."""
    folder = dialog.lineEdit_ProjectFolder.text().strip()
    if not folder or not Path(folder).is_dir():
        return "empty"
    return "processed" if dialog.project_directory == folder else "added"


def _domain_setup(dialog, _node):
    return "processed" if dialog.setup_folder and Path(dialog.setup_folder).is_dir() else "empty"


def _gauges(dialog, _node):
    """Added with pour points and their ID field; processed once Set built the drainage network."""
    from ..workflows.hydrology import domain_prepared

    if dialog.comboBox_pourPointInput.currentIndex() < 0 or not dialog.comboBox_pourPointOutletID.currentText().strip():
        return "empty"
    return "processed" if domain_prepared(dialog) else "added"


def _categorical(dialog, node):
    return "added" if dialog._categorical_modes.get(node["key"]) else "empty"


def _domain(dialog, _node):
    return "added" if dialog._domain_definition_mode else "empty"


def _meteo(dialog, node):
    return "added" if node["key"].removeprefix("meteo_") in dialog._meteo_inputs else "empty"


def _time_period(dialog, _node):
    """Processed once Set saved the simulation period."""
    from ...core.handlers.state.nml_settings import load_settings

    return "processed" if dialog.setup_folder and load_settings(dialog.setup_folder).get("time") else "empty"


def _resolution(dialog, _node):
    """Added with an L0 file; processed once Set made the common grid (still current) and the
    latlon.nc where the version needs it."""
    from ..controllers.grid import grid_current

    if not dialog._grid_l0_info:
        return "empty"
    latlon = dialog._input_findings.get("latlon")
    return "processed" if grid_current(dialog) and not (latlon and not latlon.ok) else "added"


def _namelists(dialog, _node):
    from ...core.handlers.store.paths import workspace_folder

    folder = dialog.setup_folder
    return "processed" if folder and any(Path(workspace_folder(folder)).glob("*.nml")) else "empty"


def _dem(dialog, _node):
    """Added once its Set found it on the common L0 grid; ▶ fills it and makes the derivatives."""
    return "added" if dialog.input_combo("dem").currentLayer() is not None and dialog._dem_error is None else "empty"


def _derivative(dialog, node):
    """Added once Set took its replacement raster; derived from the DEM otherwise."""
    return "added" if node["key"] in dialog._dem_replacements else _dem(dialog, node)


STATUS = {"intro": _intro, "project_setup": _project_setup, "domain_setup": _domain_setup, "gauges": _gauges,
          "categorical": _categorical, "domain": _domain, "meteo": _meteo, "time_period": _time_period,
          "resolution": _resolution, "namelists": _namelists, "dem": _dem, "derivative": _derivative}


RUNS = {"morphology": "execute_all_morphology", "meteorology": "execute_all_meteo"}


def _pending(dialog, row) -> bool:
    """Under A or B before that group's run has completed, so its files do not count yet."""
    parent = row.parent()
    workflow = RUNS.get(parent.data(0, Qt.ItemDataRole.UserRole)) if parent is not None else None
    if not workflow or not dialog.setup_folder:
        return False
    from ...core.handlers.state import processing

    return processing.workflow(dialog.setup_folder, workflow).get("status") != "completed"


def _state(dialog, row):
    """processed, added or empty; None for a row without a status."""
    node = _node(row)
    if not ({"status", "display", "input"} & node.keys()):
        return None
    if not _pending(dialog, row):
        findings = dialog._input_findings
        if any(findings[name].ok for name in _inputs(node) if name in findings):
            return "processed"
        if "display" in node and dialog.setup_folder:
            from ...core.handlers.store.morphology import resolve_display_output

            if resolve_display_output(dialog.setup_folder, node["display"]) is not None:
                return "processed"
    rule = STATUS.get(node.get("status"))
    return rule(dialog, node) if rule else "empty"


def build(dialog) -> None:
    """Columns, keys on the Designer rows, statuses, and Project Configuration selected."""
    from ..controllers import post_context

    dialog._input_findings, dialog._viewed, dialog._indicators = {}, {}, []
    tree = dialog.treeWidget_projectStructure
    tree.setColumnCount(len(COLUMNS))
    tree.setHeaderLabels(COLUMNS)
    header = tree.header()
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(28)
    header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for column in (STATUS_COLUMN, VIEW, NEXT):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        tree.setColumnWidth(column, 28)
    header.moveSection(NEXT, 0)
    tree.setTreePosition(0)  # the branches stay with the names
    for row in _rows(dialog):
        node = _node(row)
        row.setData(0, Qt.ItemDataRole.UserRole, node["key"])
        row.setForeground(NEXT, QColor("#0000FF"))
        row.setTextAlignment(NEXT, Qt.AlignmentFlag.AlignCenter)
        if "set" in node:
            indicate(dialog, node["key"], getattr(dialog, node["set"]))
    tree.expandAll()
    dialog.splitter.setSizes([310, 850])
    _action(dialog, item(dialog, "outputs"), "↺", "Reset the view's layout", lambda: post_context.reset_layout(dialog))
    set_view(dialog, "outputs", False)
    outputs_shown(dialog, False)
    refresh(dialog)
    tree.setCurrentItem(item(dialog, "project_configuration"))
    select(dialog, tree.currentItem())


def indicate(dialog, key, label) -> None:
    """The row's status again on `label`, right of its page's Set button; a row may have several
    (Precipitation: its form's and Set Periods'); the labels of closed forms drop out, and a label
    shared by rows (the DEM derivatives' page) follows the last one."""
    dialog._indicators = [(name, shown) for name, shown in dialog._indicators
                          if not sip.isdeleted(shown) and shown is not label]
    dialog._indicators.append((key, label))
    row = item(dialog, key)
    label.setText(row.text(STATUS_COLUMN))
    label.setToolTip(row.toolTip(STATUS_COLUMN))


def _action(dialog, row, symbol, tip, slot, column=STATUS_COLUMN):
    """The row's button in `column` (rows without a status or view), made on first use."""
    tree = dialog.treeWidget_projectStructure
    button = tree.itemWidget(row, column)
    if button is None:
        button = QPushButton()
        button.setFlat(True)
        button.setFixedSize(24, 24)
        button.clicked.connect(lambda _checked=False: slot())
        tree.setItemWidget(row, column, button)
    button.setText(symbol)
    button.setToolTip(tip)
    return button


def button(dialog, key):
    """The row's action button (▶ of A. Morphology, B. Meteorology, C. Observations for `workflow_state`)."""
    return dialog.treeWidget_projectStructure.itemWidget(_run_row(dialog, key), STATUS_COLUMN)


def _run_row(dialog, key):
    """Where a group's ▶ and ↺ sit: its Process row, else the row itself (C. Observations)."""
    return item(dialog, f"{key}_process") or item(dialog, key)


def item(dialog, key):
    """The row with `key`, or None."""
    return next((row for row in _rows(dialog) if row.data(0, Qt.ItemDataRole.UserRole) == key), None)


def select(dialog, row) -> None:
    """Show the row's page in `tab_project`, titled after it; a row without one selects its
    first child, and an analysis row also shows its view."""
    if row is None:
        return
    node = _node(row)
    if "page" not in node:
        dialog.treeWidget_projectStructure.setCurrentItem(row.child(0))
        return
    from ..controllers import forcing, grid, intro, main, process_cases

    for page in ("meteoVar", "precipitation"):
        main.stash(dialog, page)  # the meteo form's unsaved input survives the switch
    dialog.save_input_state()
    key = node["key"]
    if "summary" in node:
        _summary(dialog, getattr(dialog, node["summary"]), row)
    if key.startswith("meteo_"):
        forcing.show_meteo_input(dialog, key.removeprefix("meteo_"))
    elif node["page"] == "page_dem_derivatives_var":
        grid.show_derivative(dialog, key)
    else:
        hook = {"intro": intro.embed, "settings": process_cases.show,
                "domain_definition": main.show_domain_definition,
                "meteorology": forcing.populate_meteo_table, "meteorology_process": forcing.populate_meteo_table,
                "observations": forcing.populate_observation_table}
        if key in hook:
            hook[key](dialog)
    dialog.tabWidget_steps.setTabText(0, node["title"])
    dialog.stackedWidget_project.setCurrentWidget(getattr(dialog, node["page"]))
    if "analysis" in node:
        from ..controllers import post_context, post_map

        post_context.view_selected(dialog, node["analysis"])
        post_map.focus(dialog, node.get("map"))


def clicked(dialog, row, column) -> None:
    """The eye: the Outputs row's toggles the analysis layout, a processed input's its layer;
    clicking an analysis row again brings its panel back (QGIS shows the canvas when a layer
    is added)."""
    from ..controllers import post_context

    key, node = row.data(0, Qt.ItemDataRole.UserRole), _node(row)
    if key == "outputs" and column == VIEW:
        post_context.workspace(dialog, not dialog._post.workspace)
    elif column == VIEW and "view" in node and not row.icon(VIEW).isNull():
        getattr(dialog, node["view"]).click()
    elif column == VIEW and not row.icon(VIEW).isNull():
        view(dialog, key, key not in dialog._viewed)
    elif "analysis" in node:
        select(dialog, row)


def view(dialog, key, on) -> None:
    """Show a processed row's layers (a forcing on the time toolbar, a `grid` row its model
    grids), or hide them."""
    from qgis.core import QgsProject

    from ...qgis_bridge.layers.project import find_by_name

    names, node = dialog._viewed.pop(key, []), _node(item(dialog, key))
    if on and key.startswith("meteo_"):
        import numpy as np

        from ...qgis_bridge.display import meteo
        from ..controllers import post_context

        variable = key.removeprefix("meteo_")
        steps, name = meteo.show(dialog, variable)
        names = [name]
        if steps:
            starts = np.array(steps, dtype="datetime64[s]")
            post_context.time_axis(dialog, np.column_stack([starts, starts]),
                                   lambda index: meteo.show(dialog, variable, index))
    elif on and "grid" in node:
        from ...qgis_bridge.display.grid import display_grids

        names = display_grids(dialog, node["grid"])
    elif on:
        from ..controllers import main

        names = [main.display_morphology(dialog, node["display"])]
    names = [name for name in names if name]
    for name in names:
        layer = find_by_name(name)
        shown = layer and QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if shown:
            shown.setItemVisibilityChecked(on)
    if on and names:
        dialog._viewed[key] = names
    set_view(dialog, key, key in dialog._viewed)


def go_to(dialog, key) -> None:
    """Select the row with `key` (the R buttons: where a value comes from)."""
    dialog.treeWidget_projectStructure.setCurrentItem(item(dialog, key))


def show_inputs(dialog, findings) -> None:
    """Show the input rows the process cases need (`datamodel.check` findings), none under
    A, B and C before Domain Setup has run, and Gauges and Domain Definition only with gauges;
    then refresh."""
    from ...core.handlers.state import processing

    dialog._input_findings = {name: finding for finding in findings for name in finding.input.split("|")}
    set_up = bool(dialog.setup_folder) and \
        processing.workflow(dialog.setup_folder, "project_structure").get("status") == "completed"
    gauges = dialog.checkBox_gaugeOption.isChecked()
    for row in _rows(dialog):
        node = _node(row)
        names = _inputs(node)
        grouped = row.parent() is not None and _node(row.parent())["key"] in ("morphology", "meteorology", "observations")
        if names or grouped:
            row.setHidden((grouped and not set_up)
                          or (bool(names) and not any(name in dialog._input_findings for name in names))
                          or (node["key"] in ("gauges", "domain_definition") and not gauges))
    tree, row = dialog.treeWidget_projectStructure, dialog.treeWidget_projectStructure.currentItem()
    while row is not None and not _shown(row):
        row = row.parent()
    if row is not tree.currentItem():
        tree.setCurrentItem(row)  # a hidden row's page would stay shown
    refresh(dialog)


def _file(dialog, node) -> str:
    """The row's prepared file, relative to the project, or ""."""
    from ...core.handlers.store.morphology import resolve_display_output
    from ...core.meteorology.display import meteo_output_path

    folder, key, path = dialog.setup_folder, node["key"], None
    if folder and "display" in node:
        output = resolve_display_output(folder, node["display"])
        path = output.path if output else None
    elif folder and key.startswith("meteo_"):
        path = meteo_output_path(folder, key.removeprefix("meteo_"))
    return os.path.relpath(path, folder) if path else ""


def _summary(dialog, table, top) -> None:
    """The shown rows under `top` with a status or inputs: status, what needs them, prepared file;
    a wider table (morphology) also gets each input's grid and how it is fitted to L0."""
    from ..controllers.grid import summary

    top = top if top.childCount() else top.parent()  # Process lists its group
    rows = [row for row in _rows(dialog, top)
            if row is not top and _shown(row) and {"status", "input"} & _node(row).keys()]
    table.setRowCount(len(rows))
    for index, row in enumerate(rows):
        node = _node(row)
        findings = [dialog._input_findings[name] for name in _inputs(node) if name in dialog._input_findings]
        needed = ", ".join(dict.fromkeys(reason for finding in findings for reason in finding.reasons))
        details = summary(dialog, node)[:table.columnCount() - 4]
        for column, text in enumerate((row.text(0), " ".join(SYMBOLS[_state(dialog, row)]), needed, *details,
                                       _file(dialog, node))):
            table.setItem(index, column, QTableWidgetItem(text))
    for column in range(table.columnCount() - 1):  # the last one stretches
        table.resizeColumnToContents(column)


def outputs_shown(dialog, on) -> None:
    """The analysis rows, shown only while a run is open (a hidden group hides its rows)."""
    for key in ("basics", "diagnostics", "advanced"):
        item(dialog, key).setHidden(not on)


def _run(dialog, key) -> None:
    from ..workflows import meteorology, observations

    if key == "morphology":
        dialog.morphology_tasks.start_execute_all()
    else:
        (meteorology if key == "meteorology" else observations).start(dialog)


def _reset(dialog, key) -> None:
    """↺: delete what the group's ▶ prepared and its reuse records, so the next ▶ starts afresh."""
    from qgis.PyQt.QtWidgets import QMessageBox

    from ...core.executions import reset
    from ..controllers import process_cases, workflow_state

    if not dialog.setup_folder:
        return
    title = _node(item(dialog, key))["title"]
    if workflow_state.running_key(dialog) or dialog.task_coordinator.is_busy():
        QMessageBox.warning(dialog, title, "Finish or cancel the running tasks first.")
        return
    again = "\n\nSet Gauges and the domain again before ▶." if key == "morphology" else ""
    if QMessageBox.question(dialog, title, f"Delete the files the ▶ of {title} prepared (.temp and data/) and its "
                            f"reuse records, so the next ▶ prepares everything afresh?{again}") \
            != QMessageBox.StandardButton.Yes:
        return
    deleted = reset.clear(dialog.setup_folder, key, log=dialog.log_message)
    dialog.log_message(f"{title}: reset, {deleted} files deleted.")
    workflow_state.refresh_all(dialog)
    process_cases.inputs(dialog)


def _waits(dialog, row) -> bool:
    """The user's next step: a ready ▶ with inputs to prepare, an empty row, or an added one
    outside a ▶ group (the precipitation scan, latlon). Introduction is optional."""
    node, status = _node(row), row.text(STATUS_COLUMN)
    if node.get("action") == "run":
        return button(dialog, node["key"]).isEnabled() and any(
            child.text(STATUS_COLUMN) == SYMBOLS["added"][0] for child in _rows(dialog, row) if _shown(child))
    grouped = row.parent() is not None and _node(row.parent()).get("action") == "run"
    return node["key"] != "intro" and (
        status == SYMBOLS["empty"][0] or (status == SYMBOLS["added"][0] and not grouped))


def refresh(dialog) -> None:
    """Every row's status, the ▶ of its group, its eye when processed, and ➤ at the next step."""
    from ..controllers import workflow_state

    tree, runs = dialog.treeWidget_projectStructure, []
    dialog._indicators = [(name, shown) for name, shown in dialog._indicators if not sip.isdeleted(shown)]
    for row in _rows(dialog):
        node = _node(row)
        state = _state(dialog, row)
        if state is not None:
            symbol, meaning = SYMBOLS[state]
            row.setText(STATUS_COLUMN, symbol)
            row.setToolTip(STATUS_COLUMN, meaning)
            row.setTextAlignment(STATUS_COLUMN, Qt.AlignmentFlag.AlignCenter)
            for name, shown in dialog._indicators:
                if name == node["key"]:
                    shown.setText(symbol)
                    shown.setToolTip(meaning)
        if node.get("action") == "run":
            runs.append(row)
        if {"display", "input", "grid", "view"} & node.keys():
            if state == "processed":
                set_view(dialog, node["key"], node["key"] in dialog._viewed)
            else:
                row.setIcon(VIEW, QIcon())
                dialog._viewed.pop(node["key"], None)
            if "view" in node:
                row.setToolTip(VIEW, getattr(dialog, node["view"]).toolTip())
    for row in runs:
        needed = [child for child in _rows(dialog, row) if child is not row and not child.isHidden()
                  and "status" in _node(child)]
        ready = bool(needed) and all(child.text(STATUS_COLUMN) != "⚪" for child in needed)
        key = _node(row)["key"]
        if key == "observations":
            ready = bool(dialog._observation_inputs)
        _action(dialog, _run_row(dialog, key), "▶", "Run", lambda key=key: _run(dialog, key)).setEnabled(
            ready and workflow_state.running_key(dialog) is None)
        _action(dialog, _run_row(dialog, key), "↺", "Reset: delete what ▶ prepared, so it prepares everything afresh",
                lambda key=key: _reset(dialog, key), VIEW)
    failed = item(dialog, dialog._failed_step) if dialog._failed_step else None
    pointed = failed if failed is not None and _shown(failed) else next(
        (row for row in _rows(dialog) if _shown(row) and _waits(dialog, row)), item(dialog, "execution"))
    if _node(pointed).get("action") == "run":
        pointed = _run_row(dialog, _node(pointed)["key"])
    for row in _rows(dialog):
        row.setText(NEXT, "➤" if row is pointed else "")
    current = tree.currentItem()
    if current is not None and "summary" in _node(current):
        _summary(dialog, getattr(dialog, _node(current)["summary"]), current)


def set_view(dialog, key, visible) -> None:
    """The row's eye: its data shown on the map, or hidden."""
    row = item(dialog, key)
    row.setIcon(VIEW, QIcon(str(ICONS / ("eye.svg" if visible else "eye-off.svg"))))
    row.setToolTip(VIEW, "Shown on the map" if visible else "Hidden")
