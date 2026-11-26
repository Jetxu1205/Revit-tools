# -*- coding: utf-8 -*-
"""Refresh shared-template slab colors and reference-format legends."""
import slab_logic
import slab_revit
import slab_ui
try:
    reload
except NameError:
    from importlib import reload
reload(slab_logic)
reload(slab_revit)
reload(slab_ui)
slab_ui.run(shift=bool(globals().get('__shiftclick__', False)))
