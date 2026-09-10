# -*- coding: utf-8 -*-
"""Shims for QGIS and Qt APIs that changed between supported versions.

Every helper here exists because the same call spells differently across the
QGIS releases the plugin runs on. Keeping them together means there is one
place to delete from once a minimum version is agreed.
"""
from __future__ import annotations


def map_layer_filters(*filter_names):
    """Return map layer filter flags, preferring the modern `Qgis.LayerFilter`."""
    from qgis.core import Qgis, QgsMapLayerProxyModel

    layer_filter_enum = getattr(Qgis, "LayerFilter", None)
    if layer_filter_enum is not None:
        filters = None
        for filter_name in filter_names:
            value = getattr(layer_filter_enum, filter_name)
            filters = value if filters is None else filters | value
        if filters is not None:
            return filters

    legacy_filters = None
    for filter_name in filter_names:
        value = getattr(QgsMapLayerProxyModel, filter_name)
        legacy_filters = value if legacy_filters is None else legacy_filters | value
    return legacy_filters


def _qmeta_type(type_name):
    """Return a QMetaType enum value across Qt/QGIS bindings."""
    from qgis.PyQt.QtCore import QMetaType

    enum_class = getattr(QMetaType, "Type", QMetaType)
    return getattr(enum_class, type_name)


def qgs_field(name, field_type):
    """Create a QgsField across old QVariant and newer QMetaType bindings."""
    from qgis.core import QgsField

    try:
        from qgis.PyQt.QtCore import QVariant
    except ImportError:
        QVariant = None

    qmeta_name_by_qvariant_name = {
        "String": "QString",
        "Int": "Int",
        "Double": "Double",
    }
    qvariant_type_by_name = {}
    if QVariant is not None and hasattr(QVariant, "Type"):
        qvariant_type_by_name = {
            "String": QVariant.Type.String,
            "Int": QVariant.Type.Int,
            "Double": QVariant.Type.Double,
        }
    qmeta_name = qmeta_name_by_qvariant_name.get(field_type, field_type)
    candidates = [_qmeta_type(qmeta_name)]
    try:
        candidates.append(int(candidates[0]))
    except (TypeError, ValueError):
        pass
    if field_type in qvariant_type_by_name:
        candidates.append(qvariant_type_by_name[field_type])

    last_error = None
    for candidate in candidates:
        try:
            return QgsField(name, candidate)
        except TypeError as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    return QgsField(name)


def create_vector_file_writer(path, fields, geometry_type, crs,
                              driver_name="ESRI Shapefile"):
    """Create a QgsVectorFileWriter using the non-deprecated factory when present."""
    from qgis.core import QgsProject, QgsVectorFileWriter

    if hasattr(QgsVectorFileWriter, "create"):
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = driver_name
        options.fileEncoding = "UTF-8"
        try:
            writer = QgsVectorFileWriter.create(
                path,
                fields,
                geometry_type,
                crs,
                QgsProject.instance().transformContext(),
                options,
            )
            if isinstance(writer, tuple):
                return writer[0]
            return writer
        except TypeError:
            pass

    return QgsVectorFileWriter(
        path,
        "UTF-8",
        fields,
        geometry_type,
        crs,
        driver_name,
    )


__all__ = [
    "create_vector_file_writer",
    "map_layer_filters",
    "qgs_field",
]
