# -*- coding: utf-8 -*-
"""Work that runs off the GUI thread.

- `task_coordinator`: bounded QgsTask scheduling with a standalone QThread fallback.
- `meteo_workflow`: the QObject worker for the long meteorology phase.

Widgets, `QgsProject` and `QgsMapLayer` stay on the main thread. A worker gets
copied primitives and paths, and reports back through signals.

Qt/QGIS-free raster subprocesses use `core.native_worker`, outside this package.
"""
