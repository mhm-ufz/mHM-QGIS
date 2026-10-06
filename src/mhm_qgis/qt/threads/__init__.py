"""Work that runs off the GUI thread.

- `task_coordinator`: bounded scheduling on the real QGIS task manager; every
  background job, including the long meteorology phase, goes through it.

Widgets, `QgsProject` and `QgsMapLayer` stay on the main thread. A worker gets
copied primitives and paths, and reports back through signals.

Qt/QGIS-free raster subprocesses use `core.native_worker`, outside this package.
"""
