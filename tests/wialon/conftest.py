"""Fixtures for the pure (no Home Assistant) Wialon tests."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = ROOT / "custom_components" / "wialon"
API_PATH = PACKAGE_DIR / "api.py"
SCRIPT_PATH = ROOT / "scripts" / "gen_xirgo_sensors.py"
STUB_PACKAGE = "wialon_pure"

try:
    import pytest_socket  # noqa: F401  (installed with PHCC; provides socket_enabled)
except ImportError:

    @pytest.fixture
    def socket_enabled():
        """Without pytest-socket nothing blocks sockets; keep the name usable."""
        yield


def _load_api():
    spec = importlib.util.spec_from_file_location("wialon_api_standalone", API_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["wialon_api_standalone"] = module
    spec.loader.exec_module(module)
    return module


def _stub_package() -> None:
    """Synthetic parent package so relative imports work without __init__."""
    if STUB_PACKAGE not in sys.modules:
        package = types.ModuleType(STUB_PACKAGE)
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules[STUB_PACKAGE] = package


@pytest.fixture(scope="session")
def api():
    """The api module, loaded without importing the integration package."""
    return _load_api()


@pytest.fixture(scope="session")
def params():
    """The params module, loaded without importing the integration package."""
    _stub_package()
    return importlib.import_module(f"{STUB_PACKAGE}.params")


@pytest.fixture(scope="session")
def history():
    """The history module (pure aggregation)."""
    _stub_package()
    return importlib.import_module(f"{STUB_PACKAGE}.history")


@pytest.fixture(scope="session")
def track():
    """The track module (pure GPS track building)."""
    _stub_package()
    return importlib.import_module(f"{STUB_PACKAGE}.track")


@pytest.fixture(scope="session")
def xirgo():
    """The generated xirgo_sensors module."""
    _stub_package()
    return importlib.import_module(f"{STUB_PACKAGE}.xirgo_sensors")


@pytest.fixture(scope="session")
def gen_script():
    """The generator script as a module (no network is touched on import)."""
    spec = importlib.util.spec_from_file_location("gen_xirgo_sensors", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_xirgo_sensors"] = module
    spec.loader.exec_module(module)
    return module
