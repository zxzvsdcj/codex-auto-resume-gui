#!/usr/bin/env python3
"""Codex Auto Resume GUI 入口。

用法：
    python gui.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_auto_resume.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
