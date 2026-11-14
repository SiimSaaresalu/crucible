"""Hand-written reference policies, one module per task. Every module here is imported.

A new reference policy (for example from `nightshift new-env`) needs no edit to this file.
"""

import importlib
import pkgutil

for _module in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
    if not _module.ispkg:
        importlib.import_module(f"{__name__}.{_module.name}")
