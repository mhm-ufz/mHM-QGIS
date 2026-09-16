"""Fixed wiring for the meteorology details form."""
from ..controllers import meteo_var_details_input as controller


def bind(dialog):
    """Connect the browser and accumulation controls."""
    dialog.pushButton_browseMeteoFolder.clicked.connect(lambda: controller.browse(dialog))
    dialog.checkBox_cumulative.toggled.connect(dialog.comboBox_cumulativeTemporalResolution.setEnabled)
    dialog.comboBox_temporalResolutionInput.currentTextChanged.connect(
        lambda: controller.populate_output_resolutions(dialog))
    dialog.comboBox_temporalResolutionOutput.currentTextChanged.connect(
        lambda: controller.update_aggregation_control(dialog))
