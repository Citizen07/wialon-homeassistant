"""Config flow for Wialon."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web
import voluptuous as vol

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import UnknownFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    WialonAuthError,
    WialonClient,
    WialonConnectionError,
    WialonError,
    WialonNotWialonError,
    build_login_url,
    message_time,
    normalize_origin,
    probe_api,
    resolve_api_url,
)
from .const import (
    CONF_API_URL,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_UNITS,
    CONF_USER_NAME,
    CONF_WEB_URL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WEB_URLS,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    OAUTH_URL_BASE,
    OAUTH_VIEW_REGISTERED,
)

_LOGGER = logging.getLogger(__name__)

_PAGE = (
    "<!doctype html><html><head><meta charset='utf-8'><title>Wialon</title></head>"
    "<body><p>{message}</p>{script}</body></html>"
)


def _age(lmsg: dict | None) -> str:
    """Human readable age of the last message."""
    when = message_time(lmsg)
    if when is None:
        return "never"
    seconds = max(0, int((datetime.now(UTC) - when).total_seconds()))
    if seconds < 90:
        return "just now"
    if seconds < 5400:
        return f"{round(seconds / 60)} min ago"
    if seconds < 172800:
        return f"{round(seconds / 3600)} h ago"
    return f"{round(seconds / 86400)} d ago"


def _unit_options(items: list[dict]) -> list[SelectOptionDict]:
    return [
        SelectOptionDict(
            value=str(item["id"]),
            label=(
                f"{item.get('nm', item['id'])} — IMEI {item.get('uid', '?')}"
                f" — last message {_age(item.get('lmsg'))}"
            ),
        )
        for item in items
    ]


def _units_selector(items: list[dict]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=_unit_options(items),
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


class WialonOAuthView(HomeAssistantView):
    """Receive the redirect from the Wialon login page."""

    url = OAUTH_URL_BASE + "/{flow_id}"
    name = "api:wialon:oauth"
    requires_auth = False

    async def get(self, request: web.Request, flow_id: str) -> web.Response:
        """Forward the access token to the waiting config flow."""
        hass = request.app[KEY_HASS]
        headers = {"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}
        try:
            flow = hass.config_entries.flow.async_get(flow_id)
        except UnknownFlow:
            return web.Response(status=400, headers=headers)
        if flow.get("handler") != DOMAIN or flow.get("step_id") != "oauth":
            return web.Response(status=400, headers=headers)

        token = request.query.get("access_token")
        if not token:
            return web.Response(
                text=_PAGE.format(
                    message="Sign-in failed. Close this window and try again.",
                    script="",
                ),
                content_type="text/html",
                headers=headers,
            )
        try:
            await hass.config_entries.flow.async_configure(
                flow_id=flow_id, user_input={"access_token": token}
            )
        except UnknownFlow:
            return web.Response(status=400, headers=headers)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Wialon sign-in callback could not be processed")
            return web.Response(status=400, headers=headers)
        return web.Response(
            text=_PAGE.format(
                message="Signed in. You can close this window.",
                script="<script>window.close()</script>",
            ),
            content_type="text/html",
            headers=headers,
        )


class WialonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Wialon."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        """Initialize."""
        self._web_url = ""
        self._api_url = ""
        self._token = ""
        self._user_name = ""
        self._unit_items: list[dict] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> WialonOptionsFlow:
        """Return the options flow."""
        return WialonOptionsFlow()

    # --- URL step -----------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Wialon web address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            try:
                web_url = normalize_origin(user_input[CONF_WEB_URL])
                api_input = (user_input.get(CONF_API_URL) or "").strip()
                if api_input:
                    api_url = normalize_origin(api_input)
                    if not await probe_api(session, api_url):
                        raise WialonNotWialonError(api_url)
                else:
                    api_url = await resolve_api_url(session, web_url)
            except (ValueError, WialonNotWialonError):
                errors["base"] = "not_wialon"
            except WialonConnectionError:
                errors["base"] = "cannot_connect"
            else:
                self._web_url = web_url
                self._api_url = api_url
                return await self.async_step_auth()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_WEB_URL,
                    default=(user_input or {}).get(CONF_WEB_URL, DEFAULT_WEB_URLS[0]),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=DEFAULT_WEB_URLS,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Optional(
                    CONF_API_URL, default=(user_input or {}).get(CONF_API_URL, "")
                ): TextSelector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # --- sign-in method menu -------------------------------------------

    def _menu(self, step_id: str) -> ConfigFlowResult:
        options = ["token"]
        try:
            get_url(self.hass, allow_ip=True, prefer_external=False)
        except NoURLAvailableError:
            pass
        else:
            options.insert(0, "oauth")
        return self.async_show_menu(step_id=step_id, menu_options=options)

    async def async_step_auth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to sign in."""
        return self._menu("auth")

    # --- shared sign-in steps (initial and reauth) ---------------------

    async def _validate_token(self, token: str) -> str:
        """Log in with token and return the user name."""
        client = WialonClient(async_get_clientsession(self.hass), self._api_url, token)
        user = await client.login()
        await client.logout()
        return user

    async def _token_accepted(self, token: str, user: str) -> ConfigFlowResult:
        self._token = token
        self._user_name = user
        if self.source == SOURCE_REAUTH:
            return await self.async_step_reauth_finish()
        return await self.async_step_units()

    async def async_step_oauth(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in through the Wialon login page."""
        if user_input is None:
            try:
                base_url = get_url(self.hass, allow_ip=True, prefer_external=False)
            except NoURLAvailableError:
                return self.async_abort(reason="no_url")
            if not self.hass.data.get(OAUTH_VIEW_REGISTERED):
                self.hass.http.register_view(WialonOAuthView())
                self.hass.data[OAUTH_VIEW_REGISTERED] = True
            redirect_uri = f"{base_url}{OAUTH_URL_BASE}/{self.flow_id}"
            return self.async_external_step(
                step_id="oauth", url=build_login_url(self._web_url, redirect_uri)
            )

        token = user_input["access_token"]
        try:
            user = await self._validate_token(token)
        except WialonError:
            return self.async_external_step_done(next_step_id="oauth_failed")
        self._token = token
        self._user_name = user
        return self.async_external_step_done(
            next_step_id=(
                "reauth_finish" if self.source == SOURCE_REAUTH else "units"
            )
        )

    async def async_step_oauth_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Abort after a failed OAuth sign-in."""
        return self.async_abort(reason="oauth_failed")

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Sign in with a pasted access token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            try:
                user = await self._validate_token(token)
            except WialonAuthError:
                errors["base"] = "invalid_auth"
            except WialonError:
                errors["base"] = "cannot_connect"
            else:
                return await self._token_accepted(token, user)
        return self.async_show_form(
            step_id="token",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_TOKEN): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )

    # --- unit selection and entry creation -----------------------------

    async def async_step_units(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the units to track."""
        errors: dict[str, str] = {}
        if self._unit_items is None:
            await self.async_set_unique_id(
                f"{urlsplit(self._api_url).netloc}:{self._user_name}".lower()
            )
            self._abort_if_unique_id_configured()
            client = WialonClient(
                async_get_clientsession(self.hass), self._api_url, self._token
            )
            try:
                self._unit_items = await client.list_units()
            except WialonAuthError:
                return self.async_abort(reason="invalid_auth")
            except WialonError:
                return self.async_abort(reason="cannot_connect")
            finally:
                await client.logout()
            if not self._unit_items:
                return self.async_abort(reason="no_units")

        if user_input is not None:
            if not user_input[CONF_UNITS]:
                errors["base"] = "no_units_selected"
            else:
                return self.async_create_entry(
                    title=f"{self._user_name} @ {urlsplit(self._web_url).netloc}",
                    data={
                        CONF_WEB_URL: self._web_url,
                        CONF_API_URL: self._api_url,
                        CONF_TOKEN: self._token,
                        CONF_USER_NAME: self._user_name,
                    },
                    options={
                        CONF_UNITS: list(user_input[CONF_UNITS]),
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                    },
                )
        return self.async_show_form(
            step_id="units",
            data_schema=vol.Schema(
                {vol.Required(CONF_UNITS): _units_selector(self._unit_items)}
            ),
            errors=errors,
        )

    # --- reauth ---------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication."""
        self._web_url = entry_data[CONF_WEB_URL]
        self._api_url = entry_data[CONF_API_URL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to sign in again."""
        return self._menu("reauth_confirm")

    async def async_step_reauth_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Store the new token if it belongs to the same account."""
        entry = self._get_reauth_entry()
        if self._user_name.casefold() != entry.data[CONF_USER_NAME].casefold():
            return self.async_abort(reason="wrong_account")
        return self.async_update_reload_and_abort(
            entry, data_updates={CONF_TOKEN: self._token}
        )


class WialonOptionsFlow(OptionsFlow):
    """Options: tracked units and polling interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        entry = self.config_entry
        client = WialonClient(
            async_get_clientsession(self.hass),
            entry.data[CONF_API_URL],
            entry.data[CONF_TOKEN],
        )
        try:
            items = await client.list_units()
        except WialonAuthError:
            return self.async_abort(reason="invalid_auth")
        except WialonError:
            return self.async_abort(reason="cannot_connect")
        finally:
            await client.logout()

        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_UNITS]:
                errors["base"] = "no_units_selected"
            else:
                return self.async_create_entry(
                    data={
                        CONF_UNITS: list(user_input[CONF_UNITS]),
                        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    }
                )

        available = {str(item["id"]) for item in items}
        current = [
            str(u) for u in entry.options.get(CONF_UNITS, []) if str(u) in available
        ]
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_UNITS, default=current): _units_selector(items),
                    vol.Required(CONF_SCAN_INTERVAL, default=interval): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
        )
