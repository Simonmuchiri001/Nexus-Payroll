"""Desktop test configuration; Qt runs without a display server."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
