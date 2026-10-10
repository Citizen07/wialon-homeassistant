"""Config flow tests; need pytest-homeassistant-custom-component."""

from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.helpers.network import NoURLAvailableError  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from custom_components.wialon.api import (  # noqa: E402
    WialonAuthError,
    WialonConnectionError,
    WialonNotWialonError,
)
from custom_components.wialon.const import DOMAIN  # noqa: E402

# The manifest depends on recorder (for the history import), so HA sets it up
# before the flow; recorder_mock must come before hass.
pytestmark = pytest.mark.usefixtures("recorder_mock", "enable_custom_integrations")

API = "custom_components.wialon.config_flow"
TOKEN = "T" * 72
UNITS = [
    {"id": 11, "nm": "Truck", "uid": "350001", "lmsg": {"t": 1700000000}},
    {"id": 12, "nm": "Van", "uid": "350002", "lmsg": None},
]


@pytest.fixture(autouse=True)
def _no_setup():
    with patch("custom_components.wialon.async_setup_entry", return_value=True):
        yield


@pytest.fixture
def client_mock():
    with (
        patch(f"{API}.WialonClient.login", AsyncMock(return_value="alice")) as login,
        patch(f"{API}.WialonClient.logout", AsyncMock()),
        patch(f"{API}.WialonClient.list_units", AsyncMock(return_value=UNITS)),
    ):
        yield login


@pytest.fixture
def resolve_mock():
    with patch(
        f"{API}.resolve_api_url", AsyncMock(return_value="https://hst-api.wialon.com")
    ) as m:
        yield m


async def _start(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"web_url": "https://hosting.wialon.com", "api_url": ""}
    )


async def test_user_not_wialon(hass: HomeAssistant) -> None:
    with patch(f"{API}.resolve_api_url", AsyncMock(side_effect=WialonNotWialonError)):
        result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "not_wialon"}


async def test_user_cannot_connect(hass: HomeAssistant) -> None:
    with patch(
        f"{API}.resolve_api_url", AsyncMock(side_effect=WialonConnectionError)
    ):
        result = await _start(hass)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_explicit_api_url_probed(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(f"{API}.probe_api", AsyncMock(return_value=False)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"web_url": "https://x.example", "api_url": "api.example"},
        )
    assert result["errors"] == {"base": "not_wialon"}


async def test_token_path_creates_entry(
    hass: HomeAssistant, client_mock, resolve_mock
) -> None:
    with patch(f"{API}.get_url", side_effect=NoURLAvailableError):
        result = await _start(hass)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["token"]  # no HA URL -> no oauth
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "token"}
    )
    assert result["step_id"] == "token"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"token": TOKEN}
    )
    assert result["step_id"] == "units"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"units": []}
    )
    assert result["errors"] == {"base": "no_units_selected"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"units": ["11"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "alice @ hosting.wialon.com"
    assert result["data"] == {
        "web_url": "https://hosting.wialon.com",
        "api_url": "https://hst-api.wialon.com",
        "token": TOKEN,
        "user_name": "alice",
    }
    assert result["options"] == {"units": ["11"], "scan_interval": 60}
    entry = result["result"]
    assert entry.unique_id == "hst-api.wialon.com:alice"


async def test_token_invalid_auth(hass: HomeAssistant, resolve_mock) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "token"}
    )
    with patch(f"{API}.WialonClient.login", AsyncMock(side_effect=WialonAuthError)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"token": "bad"}
        )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_duplicate_aborts(hass: HomeAssistant, client_mock, resolve_mock) -> None:
    MockConfigEntry(
        domain=DOMAIN, unique_id="hst-api.wialon.com:alice", data={}
    ).add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "token"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"token": TOKEN}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_no_units_aborts(hass: HomeAssistant, client_mock, resolve_mock) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "token"}
    )
    with patch(f"{API}.WialonClient.list_units", AsyncMock(return_value=[])):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"token": TOKEN}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_units"


async def _with_url(hass: HomeAssistant) -> None:
    await hass.config.async_update(internal_url="http://ha.local:8123")


async def test_oauth_flow_and_callback_view(
    hass: HomeAssistant, hass_client_no_auth, client_mock, resolve_mock
) -> None:
    await _with_url(hass)
    assert await async_setup_component(hass, "http", {})
    result = await _start(hass)
    assert result["menu_options"] == ["oauth", "token"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "oauth"}
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    flow_id = result["flow_id"]
    assert "access_type=768" in result["url"]
    assert f"%2Fapi%2Fwialon%2Foauth%2F{flow_id}" in result["url"]

    http = await hass_client_no_auth()
    # unknown flow id
    resp = await http.get("/api/wialon/oauth/nope", params={"access_token": TOKEN})
    assert resp.status == 400
    # failed sign-in page (no token) leaves the flow waiting
    resp = await http.get(f"/api/wialon/oauth/{flow_id}", params={"svc_error": "4"})
    assert resp.status == 200
    assert "failed" in await resp.text()
    assert hass.config_entries.flow.async_get(flow_id)["step_id"] == "oauth"
    # success
    resp = await http.get(f"/api/wialon/oauth/{flow_id}", params={"access_token": TOKEN})
    assert resp.status == 200
    assert "window.close()" in await resp.text()
    # Without the callback this would re-enter the oauth step instead.
    result = await hass.config_entries.flow.async_configure(flow_id)
    assert result["step_id"] == "units"


async def test_oauth_view_rejects_flow_not_waiting_for_oauth(
    hass: HomeAssistant, hass_client_no_auth, client_mock, resolve_mock
) -> None:
    await _with_url(hass)
    assert await async_setup_component(hass, "http", {})
    result = await _start(hass)  # sits at the sign-in menu, not at oauth
    # A second flow reaching oauth registers the view (it is registered lazily).
    other = await _start(hass)
    other = await hass.config_entries.flow.async_configure(
        other["flow_id"], {"next_step_id": "oauth"}
    )
    assert other["type"] is FlowResultType.EXTERNAL_STEP
    http = await hass_client_no_auth()
    resp = await http.get(
        f"/api/wialon/oauth/{result['flow_id']}", params={"access_token": TOKEN}
    )
    assert resp.status == 400
    assert hass.config_entries.flow.async_get(result["flow_id"])["step_id"] == "auth"


async def test_oauth_invalid_token_aborts(
    hass: HomeAssistant, hass_client_no_auth, resolve_mock
) -> None:
    await _with_url(hass)
    assert await async_setup_component(hass, "http", {})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "oauth"}
    )
    flow_id = result["flow_id"]
    http = await hass_client_no_auth()
    with patch(f"{API}.WialonClient.login", AsyncMock(side_effect=WialonAuthError)):
        resp = await http.get(f"/api/wialon/oauth/{flow_id}", params={"access_token": "x"})
    assert resp.status == 200
    result = await hass.config_entries.flow.async_configure(flow_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "oauth_failed"


def _entry(**kw) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="hst-api.wialon.com:alice",
        data={
            "web_url": "https://hosting.wialon.com",
            "api_url": "https://hst-api.wialon.com",
            "token": TOKEN,
            "user_name": "alice",
        },
        options={"units": ["11", "99"], "scan_interval": 60},
        **kw,
    )


async def test_reauth_success(hass: HomeAssistant, client_mock) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "token"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"token": "N" * 72}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["token"] == "N" * 72


async def test_reauth_wrong_account(hass: HomeAssistant, client_mock) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "token"}
    )
    with patch(f"{API}.WialonClient.login", AsyncMock(return_value="mallory")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"token": "N" * 72}
        )
    assert result["reason"] == "wrong_account"
    assert entry.data["token"] == TOKEN


async def test_options_flow(hass: HomeAssistant, client_mock) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    # unit 99 is no longer on the account, so it is dropped from the default
    schema_keys = {str(k): k for k in result["data_schema"].schema}
    assert schema_keys["units"].default() == ["11"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"units": ["11", "12"], "scan_interval": 120}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"units": ["11", "12"], "scan_interval": 120}
