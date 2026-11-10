# -*- coding: utf-8 -*-
"""Pytest: пути — корень репо и scripts/ (мок-API импортируется по имени)."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
