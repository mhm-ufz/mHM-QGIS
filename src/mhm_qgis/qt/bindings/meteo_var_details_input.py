"""Fixed wiring for the meteorology details form."""
from ..controllers import meteo_var_details_input as controller


def bind(dialog):
    """Connect the browser and accumulation controls."""
    dialog.pushButton_browseMeteoFolder.clicked.connect(lambda: controller.browse(dialog))
    dialog.checkBox_cumulative.toggled.connect(dialog.comboBox_cumulativeTemporalResolution.setEnabled)
