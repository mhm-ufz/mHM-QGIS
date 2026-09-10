# -*- coding: utf-8 -*-
"""Load Designer forms with the Qt binding supplied by QGIS."""
from pathlib import Path

from qgis.PyQt import uic


def load_form(filename):
    return uic.loadUiType(str(Path(__file__).parent / "forms" / filename))[0]
