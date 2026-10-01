"""BGYHub Mailbox Admin.

The bundled pure-Python dependencies in ../vendor are put first on sys.path,
so the app never needs pip or a virtual environment.
"""

import sys
from pathlib import Path

_VENDOR = str(Path(__file__).resolve().parent.parent / "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
