"""Post-processing docks around QGIS Desktop's main window or the standalone map window.

Both hosts are `QMainWindow`s: docks are split, tabified, resized and their state
saved the same way. Only registering and removing a dock goes through `iface` in
Desktop, so QGIS's own panel bookkeeping sees it. The central widget (QGIS's main
canvas, or the standalone window's tree and canvas) is never replaced.

Each view of the Outputs tab's navigation list has a layout in `layouts.yml`; the
user's adjustments are saved per view (`saveState`), and the workspace keeps the
user's own layout to restore when it is turned off (post plan §0b).
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from qgis.PyQt.QtCore import QSettings, Qt
from qgis.PyQt.QtWidgets import QToolBar, QWidget

from ..ui import load_form

STATE_VERSION = 1
SETTINGS = "mhm_qgis/post"
AREAS = {"right": Qt.DockWidgetArea.RightDockWidgetArea, "bottom": Qt.DockWidgetArea.BottomDockWidgetArea}
SPLIT = {"right": Qt.Orientation.Vertical, "bottom": Qt.Orientation.Horizontal}
RESIZE = {"right": Qt.Orientation.Horizontal, "bottom": Qt.Orientation.Vertical}


@cache
def layouts() -> dict:
    return yaml.safe_load(Path(__file__).with_name("layouts.yml").read_text(encoding="utf-8"))


def view_for(title):
    """The view key answering a navigation entry's text; None for a header."""
    return next((key for key, spec in layouts()["views"].items() if spec["title"] == title), None)


def settings() -> QSettings:
    return QSettings()


def window(dialog):
    """QGIS Desktop's main window, or the standalone map window."""
    return dialog.iface.mainWindow() if dialog.iface is not None else dialog.map_canvas_window()


def _form(dialog, form_file):
    """A widget built from a Designer form; its child widgets join `dialog._post.ui`."""
    widget = QWidget()
    ui = load_form(form_file)()
    ui.setupUi(widget)
    for name, value in vars(ui).items():
        setattr(dialog._post.ui, name, value)
    return widget


def dock(dialog, name, on_create=None):
    """The named dock, built from its form on first use and registered with the host."""
    from qgis.gui import QgsDockWidget

    docks = dialog._post.docks
    if name not in docks:
        form_file, title = layouts()["docks"][name]
        widget = _form(dialog, form_file)
        created = QgsDockWidget(f"mHM post: {title}", window(dialog))
        created.setObjectName("mhmPost" + "".join(part.title() for part in name.split("_")) + "Dock")
        created.setWidget(widget)
        docks[name] = created
        if dialog.iface is not None:
            dialog.iface.addDockWidget(AREAS["right"], created)
        else:
            window(dialog).addDockWidget(AREAS["right"], created)
        created.hide()
        if on_create is not None:
            on_create(dialog, name)
    return docks[name]


def time_toolbar(dialog, on_create=None):
    """The "mHM post time" toolbar, built from its form on first use."""
    state = dialog._post
    if state.toolbar is None:
        toolbar = QToolBar("mHM post time", window(dialog))
        toolbar.setObjectName("mhmPostTimeToolbar")
        toolbar.addWidget(_form(dialog, "post_time_toolbar.ui"))
        if dialog.iface is not None:
            dialog.iface.addToolBar(toolbar)
        else:
            window(dialog).addToolBar(toolbar)
        state.toolbar = toolbar
        if on_create is not None:
            on_create(dialog)
    return state.toolbar


def names(view) -> list[str]:
    spec = layouts()["views"][view]
    return [name for area in AREAS for stack in spec.get(area, []) for name in stack]


def apply(dialog, view, on_create=None, reset=False) -> None:
    """Arrange the host for `view`: its saved state, or its declared layout."""
    host, docks = window(dialog), dialog._post.docks
    wanted = names(view)
    for name in wanted:
        dock(dialog, name, on_create)
    for name, other in docks.items():
        if name not in wanted:
            other.hide()
    saved = None if reset else settings().value(f"{SETTINGS}/layout/{view}")
    if saved is not None and host.restoreState(saved, STATE_VERSION):
        for name in wanted:
            docks[name].show()
        return
    spec = layouts()["views"][view]
    for area, stacks in ((area, spec.get(area, [])) for area in AREAS):
        previous = None
        for stack in stacks:
            first = docks[stack[0]]
            if previous is None:
                host.addDockWidget(AREAS[area], first)
            else:
                host.splitDockWidget(previous, first, SPLIT[area])
            first.show()
            for name in stack[1:]:
                host.tabifyDockWidget(first, docks[name])
                docks[name].show()
            first.raise_()
            previous = first
        if len(stacks) > 1:  # split stacks share their area equally
            host.resizeDocks([docks[stack[0]] for stack in stacks], [1000] * len(stacks), SPLIT[area])
        if stacks and area in spec.get("sizes", {}):
            host.resizeDocks([docks[stacks[0][0]]], [int(spec["sizes"][area])], RESIZE[area])


def save_view(dialog, view) -> None:
    if view is not None:
        settings().setValue(f"{SETTINGS}/layout/{view}", window(dialog).saveState(STATE_VERSION))


def forget_view(dialog, view) -> None:
    settings().remove(f"{SETTINGS}/layout/{view}")


def begin(dialog) -> None:
    """Keep the user's layout (unless an earlier session left one unrestored)."""
    store = settings()
    if store.value(f"{SETTINGS}/user_layout") is None:
        store.setValue(f"{SETTINGS}/user_layout", window(dialog).saveState(STATE_VERSION))
    if dialog.iface is None:
        window(dialog).show()


def end(dialog) -> None:
    """Remove and delete our docks and toolbar, then restore the user's layout."""
    state, host = dialog._post, window(dialog)
    for created in state.docks.values():
        if dialog.iface is not None:
            dialog.iface.removeDockWidget(created)
        else:
            host.removeDockWidget(created)
        created.deleteLater()
    if state.toolbar is not None:
        host.removeToolBar(state.toolbar)
        state.toolbar.deleteLater()
    state.docks, state.toolbar = {}, None
    store = settings()
    saved = store.value(f"{SETTINGS}/user_layout")
    if saved is not None:
        host.restoreState(saved, STATE_VERSION)
        store.remove(f"{SETTINGS}/user_layout")
