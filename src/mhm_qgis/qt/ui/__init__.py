"""Load Designer forms with the Qt binding supplied by QGIS."""
from pathlib import Path

from qgis.PyQt import uic

# On the whole box: a rule on ::indicator would replace the native check mark.
CHECKBOX_STYLE = "QCheckBox { border: 1px solid #8ec5f0; border-radius: 3px; padding: 1px 3px; }"


def load_form(filename):
    form = uic.loadUiType(str(Path(__file__).parent / "forms" / filename))[0]

    class Styled(form):
        def setupUi(self, widget):
            super().setupUi(widget)
            widget.setStyleSheet(f"{widget.styleSheet()}\n{CHECKBOX_STYLE}")  # cascades to the form's checkboxes

    return Styled
