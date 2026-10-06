"""Connect the widgets of each form to the methods that answer them.

One module per form. Each exposes ``bind(dialog)``, called from that dialog's
``__init__``. Only wiring that is fixed by the form lives here: connections made
against widgets built at runtime, against ``QProcess``, the task coordinator or a
map tool are made where those objects are created.
"""
