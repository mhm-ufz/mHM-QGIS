"""Morphology computation: plain functions, no Qt, no QGIS, no dialog.

What each step of the morphology workflow actually does, expressed as functions
over paths and headers. Sequencing lives in `core/executions/morphology`; layer and
Processing work lives in `qgis_bridge`; anything a user must be told travels out
through a `report=` callback.
"""
