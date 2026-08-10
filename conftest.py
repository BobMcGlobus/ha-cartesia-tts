"""Root test configuration.

``tests/unit`` runs against fakes and needs nothing but aiohttp, so it works on
any Python that this repository supports. ``tests/ha`` boots a real Home
Assistant, which requires Python 3.14+ and
``pytest-homeassistant-custom-component``. When that is not installed the Home
Assistant tests are skipped instead of failing collection, so the fast tests
stay runnable locally.

``pytest_plugins`` has to live in the rootdir conftest, which is why this file
sits next to ``pyproject.toml`` rather than inside ``tests/``.
"""

from __future__ import annotations

from importlib.util import find_spec

if find_spec("pytest_homeassistant_custom_component") is None:
    # Ignore the whole directory, so its conftest is not imported either.
    collect_ignore = ["tests/ha"]
else:
    pytest_plugins = "pytest_homeassistant_custom_component"
