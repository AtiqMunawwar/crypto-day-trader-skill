"""Load a skill script (folder names contain hyphens)."""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures", "binance")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def load(folder, name):
    path = os.path.join(ROOT, folder, name + ".py")
    mod_name = "skill_" + folder.replace("-", "_") + "_" + name
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
