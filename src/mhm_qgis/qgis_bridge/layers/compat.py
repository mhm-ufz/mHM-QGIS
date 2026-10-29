"""Shims for QGIS and Qt APIs that changed between supported versions.

Every helper here exists because the same call spells differently across the
QGIS releases the plugin runs on. Keeping them together means there is one
place to delete from once a minimum version is agreed.
"""
from __future__ import annotations

import contextlib


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
    with contextlib.suppress(TypeError, ValueError):
        candidates.append(int(candidates[0]))
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


def linear_interpolation():
    """The colour ramp shader's linear mode: `Qgis.ShaderInterpolationMethod` from QGIS 3.38."""
    from qgis.core import Qgis, QgsColorRampShader

    method = getattr(Qgis, "ShaderInterpolationMethod", None)
    return method.Linear if method is not None else QgsColorRampShader.Interpolated


def polygon_geometry():
    """`Qgis.GeometryType.Polygon` from QGIS 3.30, `QgsWkbTypes.PolygonGeometry` before."""
    from qgis.core import Qgis, QgsWkbTypes

    kind = getattr(Qgis, "GeometryType", None)
    return kind.Polygon if kind is not None else QgsWkbTypes.PolygonGeometry


def input_band(renderer) -> int:
    """The band a single-band renderer draws: `inputBand()` from QGIS 3.38, `band()` before."""
    return renderer.inputBand() if hasattr(renderer, "inputBand") else renderer.band()


def set_input_band(renderer, band) -> None:
    """Switch a single-band renderer's band: `setInputBand` from QGIS 3.38, `setBand` before."""
    (renderer.setInputBand if hasattr(renderer, "setInputBand") else renderer.setBand)(band)


__all__ = [
    "create_vector_file_writer",
    "input_band",
    "linear_interpolation",
    "polygon_geometry",
    "qgs_field",
    "set_input_band",
]
