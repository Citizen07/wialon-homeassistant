"""Fixtures for the pure (no Home Assistant) Wialon API tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

API_PATH = (
    Path(__file__).resolve().parents[2] / "custom_components" / "wialon" / "api.py"
)


def _load_api():
    spec = importlib.util.spec_from_file_location("wialon_api_standalone", API_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["wialon_api_standalone"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def api():
    """The api module, loaded without importing the integration package."""
    return _load_api()
