from __future__ import annotations

import atexit
import os
import sys
import tempfile
from pathlib import Path

TEST_ASTRBOT_ROOT = tempfile.TemporaryDirectory(prefix="astrbot-imgexploration-tests-")
atexit.register(TEST_ASTRBOT_ROOT.cleanup)
os.environ["ASTRBOT_ROOT"] = TEST_ASTRBOT_ROOT.name

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASTRBOT_ROOT = PLUGIN_ROOT.parents[2]
PLUGIN_PARENT = PLUGIN_ROOT.parent

for import_root in (ASTRBOT_ROOT, PLUGIN_PARENT):
    import_root_str = str(import_root)
    if import_root_str not in sys.path:
        sys.path.insert(0, import_root_str)
