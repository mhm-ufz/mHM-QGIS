# -*- coding: utf-8 -*-
"""Widget state for the forms: read, populate, enable, validate.

Functions take the dialog as their first argument, so behaviour is testable
without constructing the dialog's whole collaborator graph. The main form splits
by concern (`main`, `grid`, `forcing`, `categorical`, `input_state`, `project`,
`model`, `time`, `version`, `workflow_state`); a small dialog keeps its behaviour
as its own methods instead.

The split against its neighbours:

- `qt/bindings` connects a signal to a slot and nothing else.
- `qt/controllers` (here) owns what a widget shows and whether it is enabled.
- `qgis_bridge` drives the QGIS application itself -- canvas, layers, project.
- `core` computes without knowing Qt or QGIS exist.

Controllers call each other directly. A dialog keeps a one-line method only for
a name another package calls, because that package holds the dialog.
"""
