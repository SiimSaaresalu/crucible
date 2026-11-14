"""Built-in tasks. Every subpackage is imported here, and importing a task package registers it.

A new task (for example from `nightshift new-env`) needs no edit to this file.
"""

import importlib
import pkgutil

for _module in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
    if _module.ispkg:
        importlib.import_module(f"{__name__}.{_module.name}")
