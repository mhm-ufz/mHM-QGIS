"""Long-lived Qt objects that are neither a form nor its wiring.

A `QObject` that owns a signal connection and some state of its own, attached
to a widget rather than defined by one -- the viewport range controller watches
a map canvas and keeps a renderer in step with it. Those do not belong in
`dialogs`, `bindings` or `controllers`, all of which are keyed to a single form.
"""
