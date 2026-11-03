"""The project tree: each row shows its page of `stackedWidget_project` in the dialog's
one tab (`tab_project`), titled after the row.

Rows are made in Designer; `tree.yml` gives each its key, page, status, inputs, display,
action and summary table, matched by title. Columns: ➤ next step | name | status or ▶ | eye.
"""
from __future__ import annotations

import os
from functools import cache
from pathlib import Path

import yaml
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import QHeaderView, QPushButton, QTableWidgetItem

COLUMNS = ("Project structure", "", "", "")  # name | status or ▶ | view | next step (shown first)
STATUS_COLUMN, VIEW, NEXT = 1, 2, 3
SYMBOLS = {"empty": ("⚪", "Empty: no inputs added"),
           "added": ("🟢", "Inputs added, not processed yet"),
           "processed": ("✅", "Processed")}
ICONS = Path(__file__).parents[1] / "ui" / "forms" / "Resources"


@cache
def load() -> dict:
    """`tree.yml` as {title: node}, nested rows included."""
    nodes, pending = {}, list(yaml.safe_load(Path(__file__).with_name("tree.yml").read_text(encoding="utf-8"))["nodes"])
    while pending:
        node = pending.pop()
        nodes[node["title"]] = node
        pending += node.get("children", [])
    return nodes


def _node(row) -> dict:
    return load()[row.text(0)]


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
    folder = dialog.lineEdit_ProjectFolder.text().strip()
    return "processed" if folder and Path(folder).is_dir() else "empty"


def _gauges(dialog, _node):
    return "added" if dialog.comboBox_pourPointInput.currentIndex() >= 0 and dialog.comboBox_pourPointOutletID.currentText().strip() else "empty"


def _categorical(dialog, node):
    return "added" if dialog._categorical_modes.get(node["key"]) else "empty"


def _domain(dialog, _node):
    return "added" if dialog._domain_definition_mode else "empty"


def _meteo(dialog, node):
    return "added" if node["key"].removeprefix("meteo_") in dialog._meteo_inputs else "empty"


def _l0(dialog, _node):
    return "processed" if dialog._grid_l0_info else "empty"


def _l2(dialog, _node):
    """Processed while the saved common grid still matches its inputs, as Execute All requires."""
    from ..controllers.grid import grid_fingerprint, precipitation_folder

    saved = (dialog._grid_l2_metadata or {}).get("configuration_fingerprint")
    try:
        current = bool(dialog._grid_l2_header) and saved == grid_fingerprint(dialog)
    except (OSError, ValueError):  # precipitation not configured
        current = False
    return "processed" if current else "added" if precipitation_folder(dialog) else "empty"


def _resolution(dialog, _node):
    """L1 and L11 set; processed once latlon.nc exists where the version needs it (v5.13)."""
    from ..controllers.grid import current_l1_resolution, current_l11_resolution

    if not (current_l1_resolution(dialog) and current_l11_resolution(dialog)):
        return "empty"
    latlon = dialog._input_findings.get("latlon")
    return "added" if latlon and not latlon.ok else "processed"


def _namelists(dialog, _node):
    from ...core.handlers.store.paths import workspace_folder

    folder = dialog.project_folder
    return "processed" if folder and any(Path(workspace_folder(folder)).glob("*.nml")) else "empty"


def _dem(dialog, _node):
    """Added once the DEM is read; ▶ fills it and makes the derivatives."""
    return "added" if dialog._grid_l0_info else "empty"


STATUS = {"intro": _intro, "project_setup": _project_setup, "gauges": _gauges,
          "categorical": _categorical, "domain": _domain, "meteo": _meteo, "l0": _l0, "l2": _l2,
          "resolution": _resolution, "namelists": _namelists, "dem": _dem}


def _state(dialog, node):
    """processed, added or empty; None for a row without a status."""
    if not ({"status", "display", "input"} & node.keys()):
        return None
    findings = dialog._input_findings
    if any(findings[name].ok for name in _inputs(node) if name in findings):
        return "processed"
    if "display" in node and dialog.project_folder:
        from ...core.handlers.store.morphology import resolve_display_output

        if resolve_display_output(dialog.project_folder, node["display"]) is not None:
            return "processed"
    rule = STATUS.get(node.get("status"))
    return rule(dialog, node) if rule else "empty"


def build(dialog) -> None:
    """Columns, keys on the Designer rows, statuses, and Project Configuration selected."""
    from ..controllers import post_context

    dialog._input_findings, dialog._viewed = {}, {}
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
        row.setData(0, Qt.ItemDataRole.UserRole, _node(row)["key"])
        row.setForeground(NEXT, QColor("#0000FF"))
        row.setTextAlignment(NEXT, Qt.AlignmentFlag.AlignCenter)
    tree.expandAll()
    dialog.splitter.setSizes([310, 850])
    _action(dialog, item(dialog, "outputs"), "↺", "Reset the view's layout", lambda: post_context.reset_layout(dialog))
    set_view(dialog, "outputs", False)
    outputs_shown(dialog, False)
    refresh(dialog)
    tree.setCurrentItem(item(dialog, "project_configuration"))
    select(dialog, tree.currentItem())


def _action(dialog, row, symbol, tip, slot):
    """The row's button in the status column (rows without a status), made on first use."""
    tree = dialog.treeWidget_projectStructure
    button = tree.itemWidget(row, STATUS_COLUMN)
    if button is None:
        button = QPushButton()
        button.setFlat(True)
        button.setFixedSize(24, 24)
        button.clicked.connect(lambda _checked=False: slot())
        tree.setItemWidget(row, STATUS_COLUMN, button)
    button.setText(symbol)
    button.setToolTip(tip)
    return button


def button(dialog, key):
    """The row's action button (▶ of A. Morphology / B. Meteorology for `workflow_state`)."""
    return dialog.treeWidget_projectStructure.itemWidget(item(dialog, key), STATUS_COLUMN)


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
    from ..controllers import categorical, forcing, grid, intro, main, process_cases

    key = node["key"]
    if "summary" in node:
        _summary(dialog, getattr(dialog, node["summary"]), row)
    if key in categorical.KINDS:
        categorical.show(dialog, key)
    elif key.startswith("meteo_"):
        forcing.show_meteo_input(dialog, key.removeprefix("meteo_"))
    elif node["page"] == "page_demDerivatives":
        main.show_dem_derivatives(dialog)
    else:
        hook = {"intro": intro.embed, "resolution": grid.show_resolution_summary, "settings": process_cases.show,
                "domain_definition": main.show_domain_definition}
        if key in hook:
            hook[key](dialog)
    dialog.tabWidget_steps.setTabText(0, node["title"])
    dialog.stackedWidget_project.setCurrentWidget(getattr(dialog, node["page"]))
    if "analysis" in node:
        from ..controllers import post_context

        post_context.view_selected(dialog, node["analysis"])


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
    set_up = bool(dialog.project_folder) and \
        processing.workflow(dialog.project_folder, "project_structure").get("status") == "completed"
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

    folder, key, path = dialog.project_folder, node["key"], None
    if folder and "display" in node:
        output = resolve_display_output(folder, node["display"])
        path = output.path if output else None
    elif folder and key.startswith("meteo_"):
        path = meteo_output_path(folder, key.removeprefix("meteo_"))
    return os.path.relpath(path, folder) if path else ""


def _summary(dialog, table, top) -> None:
    """The shown rows under `top` with a status or inputs: status, what needs them, prepared file."""
    rows = [row for row in _rows(dialog, top)
            if row is not top and _shown(row) and {"status", "input"} & _node(row).keys()]
    table.setRowCount(len(rows))
    for index, row in enumerate(rows):
        node = _node(row)
        findings = [dialog._input_findings[name] for name in _inputs(node) if name in dialog._input_findings]
        needed = ", ".join(dict.fromkeys(reason for finding in findings for reason in finding.reasons))
        for column, text in enumerate((row.text(0), " ".join(SYMBOLS[_state(dialog, node)]), needed, _file(dialog, node))):
            table.setItem(index, column, QTableWidgetItem(text))
    for column in range(table.columnCount() - 1):  # the last one stretches
        table.resizeColumnToContents(column)


def outputs_shown(dialog, on) -> None:
    """The analysis rows, shown only while a run is open (a hidden group hides its rows)."""
    for key in ("basics", "diagnostics", "advanced"):
        item(dialog, key).setHidden(not on)


def _run(dialog, key) -> None:
    if key == "morphology":
        dialog.morphology_tasks.start_execute_all()
    else:
        from ..workflows import meteorology

        meteorology.start(dialog)


def _waits(dialog, row) -> bool:
    """The user's next step: a ready ▶ with inputs to prepare, an empty row, or an added one
    outside a ▶ group (the precipitation scan, latlon). Introduction is optional."""
    node, status = _node(row), row.text(STATUS_COLUMN)
    if node.get("action") == "run":
        return dialog.treeWidget_projectStructure.itemWidget(row, STATUS_COLUMN).isEnabled() and any(
            child.text(STATUS_COLUMN) == SYMBOLS["added"][0] for child in _rows(dialog, row) if _shown(child))
    grouped = row.parent() is not None and _node(row.parent()).get("action") == "run"
    return node["key"] != "intro" and (
        status == SYMBOLS["empty"][0] or (status == SYMBOLS["added"][0] and not grouped))


def refresh(dialog) -> None:
    """Every row's status, the ▶ of its group, its eye when processed, and ➤ at the next step."""
    from ..controllers import workflow_state

    tree, runs = dialog.treeWidget_projectStructure, []
    for row in _rows(dialog):
        node = _node(row)
        state = _state(dialog, node)
        if state is not None:
            symbol, meaning = SYMBOLS[state]
            row.setText(STATUS_COLUMN, symbol)
            row.setToolTip(STATUS_COLUMN, meaning)
            row.setTextAlignment(STATUS_COLUMN, Qt.AlignmentFlag.AlignCenter)
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
        _action(dialog, row, "▶", "Run", lambda key=key: _run(dialog, key)).setEnabled(
            ready and workflow_state.running_key(dialog) is None)
    pointed = next((row for row in _rows(dialog) if _shown(row) and _waits(dialog, row)), item(dialog, "execution"))
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
