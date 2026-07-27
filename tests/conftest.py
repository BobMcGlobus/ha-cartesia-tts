"""Test setup.

``api.py`` is deliberately free of Home Assistant imports, so it can be tested
with nothing but aiohttp. The package is stubbed here so that its relative
imports resolve without executing ``__init__.py``, which does need Home
Assistant.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "cartesia_tts"

if "cartesia_tts" not in sys.modules:
    _package = types.ModuleType("cartesia_tts")
    _package.__path__ = [str(COMPONENT)]
    sys.modules["cartesia_tts"] = _package
