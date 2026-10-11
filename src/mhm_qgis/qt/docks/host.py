"""Post-processing views in QGIS Desktop's main window or the standalone map window.

Each view of the Outputs tab's navigation list (`layouts.yml`, post plan §0c) has a
**central panel**, a page added to the stacked widget that holds the main map canvas
(QGIS 4.2's central widget holds one; the standalone window is built alike), or the
**canvas itself** (Map Explorer), and **side docks on the left**, tabbed with QGIS's
Layers and Browser panels (a few on the right, as `layouts.yml` says). The central widget is never replaced: pages are added
and removed. Registering and removing a dock goes through `iface` in Desktop, so
QGIS's own panel bookkeeping sees it.

The user's adjustments are saved per view (`saveState`), and the workspace keeps the
user's own layout to restore when it is turned off.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml
from qgis.PyQt.QtCore import QSettings, Qt, QTimer
from qgis.PyQt.QtWidgets import QDockWidget, QStackedWidget, QToolBar, QWidget

from ..ui import load_form

STATE_VERSION = 2  # central panels and left tabs since P5e: older saved states are ignored
SETTINGS = "mhm_qgis/post"
LEFT = Qt.DockWidgetArea.LeftDockWidgetArea
RIGHT = Qt.DockWidgetArea.RightDockWidgetArea


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


def canvas(dialog):
    """The host's main map canvas."""
    return dialog.iface.mapCanvas() if dialog.iface is not None else dialog.map_canvas_window().canvas


def stack(dialog) -> QStackedWidget:
    """The stacked widget whose page 0 is the main canvas (verified on QGIS 4.2.3)."""
    holder = canvas(dialog).parentWidget()
    if not isinstance(holder, QStackedWidget):
        raise RuntimeError("The map canvas is not in a stacked widget: this QGIS is laid out differently.")
    return holder


def _form(dialog, form_file):
    """A widget built from a Designer form; its child widgets join `dialog._post.ui`."""
    widget = QWidget()
    ui = load_form(form_file)()
    ui.setupUi(widget)
    for name, value in vars(ui).items():
        setattr(dialog._post.ui, name, value)
    return widget


def _camel(name) -> str:
    return "".join(part.title() for part in name.split("_"))


def part(dialog, name, on_create=None):
    """The named central panel or side dock, made from its form on first use."""
    from qgis.gui import QgsDockWidget

    state = dialog._post
    made = state.panels.get(name) or state.docks.get(name)
    if made is not None:
        return made
    if name in layouts()["panels"]:
        made = state.panels[name] = _form(dialog, layouts()["panels"][name][0])
        made.setObjectName(f"mhmPost{_camel(name)}Panel")
        stack(dialog).addWidget(made)
    else:
        form_file, title = layouts()["docks"][name]
        made = state.docks[name] = QgsDockWidget(f"mHM post: {title}", window(dialog))
        made.setObjectName(f"mhmPost{_camel(name)}Dock")
        made.setWidget(_form(dialog, form_file))
        if dialog.iface is not None:
            dialog.iface.addDockWidget(LEFT, made)
        else:
            window(dialog).addDockWidget(LEFT, made)
        made.hide()
    if on_create is not None:
        on_create(dialog, name)
    return made


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


def central(view) -> str:
    """The view's central panel name, or "canvas"."""
    return layouts()["views"][view]["central"]


def show_central(dialog, view) -> None:
    """Show the view's panel over the canvas, or the canvas itself."""
    name = central(view)
    stack(dialog).setCurrentWidget(canvas(dialog) if name == "canvas" else dialog._post.panels[name])


def add_dock(dialog, dock, beside=None, area=LEFT) -> None:
    """Tab a dock beside `beside`; else into `area` (on the left, with QGIS's Layers panel)."""
    host = window(dialog)
    anchor = beside or (host.findChild(QDockWidget, "Layers") if area == LEFT else None)
    if anchor is not None and anchor is not dock and anchor.isVisible() and host.dockWidgetArea(anchor) == area:
        host.tabifyDockWidget(anchor, dock)
    else:
        host.addDockWidget(area, dock)
    dock.show()


def apply(dialog, view, on_create=None, reset=False) -> None:
    """Arrange the host for `view`: its panel in the centre and its docks as left tabs,
    as saved for the view or as declared."""
    host, docks, spec = window(dialog), dialog._post.docks, layouts()["views"][view]
    left, right = spec.get("left", []), spec.get("right", [])
    for name in (*left, *right):
        part(dialog, name, on_create)
    if spec["central"] != "canvas":
        part(dialog, spec["central"], on_create)
    for name, other in docks.items():
        if name not in (*left, *right):
            other.hide()
    show_central(dialog, view)
    saved = None if reset else settings().value(f"{SETTINGS}/layout/{view}")
    if saved is not None and host.restoreState(saved, STATE_VERSION):
        for name in (*left, *right):
            docks[name].show()
        return
    for area, names, width in ((LEFT, left, spec.get("width", 360)), (RIGHT, right, spec.get("right_width", 340))):
        beside = None
        for name in names:
            add_dock(dialog, docks[name], beside, area)
            beside = beside or docks[name]
        if names:
            docks[names[0]].raise_()
            QTimer.singleShot(0, docks[names[0]].raise_)  # again once the tab bar exists (QGIS Desktop)
            host.resizeDocks([docks[names[0]]], [int(width)], Qt.Orientation.Horizontal)


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
    """Remove our panels (the canvas comes back), docks and toolbar, then restore the
    user's layout."""
    state, host = dialog._post, window(dialog)
    for page in state.panels.values():
        stack(dialog).removeWidget(page)
        page.deleteLater()
    stack(dialog).setCurrentWidget(canvas(dialog))
    for created in state.docks.values():
        if dialog.iface is not None:
            dialog.iface.removeDockWidget(created)
        else:
            host.removeDockWidget(created)
        created.deleteLater()
    if state.toolbar is not None:
        host.removeToolBar(state.toolbar)
        state.toolbar.deleteLater()
    state.panels, state.docks, state.toolbar = {}, {}, None
    store = settings()
    saved = store.value(f"{SETTINGS}/user_layout")
    if saved is not None:
        host.restoreState(saved, STATE_VERSION)
        store.remove(f"{SETTINGS}/user_layout")
