"""Minimal async Wialon Remote API client (stdlib + aiohttp only)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import re
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)
UNIT_FLAGS = 1281  # 0x1 base + 0x100 advanced + 0x400 last message & position
# Polling adds 0x00100000 message parameters: the accumulated per-parameter
# store, which keeps values that the latest message omits.
POLL_FLAGS = UNIT_FLAGS | 0x00100000
AUTH_ERROR_CODES = (4, 7, 8)
ERROR_INVALID_SESSION = 1

_SDK_URL_RE = re.compile(r"""sdk_url\s*=\s*["']([^"']+)["']""")


class WialonError(Exception):
    """Generic Wialon failure."""

    def __init__(self, message: str = "", code: int | None = None) -> None:
        super().__init__(message or (f"Wialon error {code}" if code else ""))
        self.code = code


class WialonAuthError(WialonError):
    """The token was rejected."""


class WialonConnectionError(WialonError):
    """Network, timeout or non-JSON response."""


class WialonNotWialonError(WialonError):
    """The address does not look like a Wialon server."""


def normalize_origin(url: str) -> str:
    """Return scheme://host[:port] for a URL given with or without scheme."""
    url = url.strip()
    if "://" not in url:
        url = f"https://{url}"
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError(f"Invalid URL: {url!r}")
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    scheme = (parts.scheme or "https").lower()
    try:
        port = parts.port
    except ValueError as err:
        raise ValueError(f"Invalid URL: {url!r}") from err
    return f"{scheme}://{host}" + (f":{port}" if port else "")


def _ajax_url(api_origin: str) -> str:
    return f"{api_origin}/wialon/ajax.html"


async def _probe(session: aiohttp.ClientSession, api_origin: str) -> bool:
    """Probe; raise WialonConnectionError if the host cannot be reached."""
    try:
        async with session.get(
            _ajax_url(api_origin),
            params={"svc": "core/search_items", "params": "{}"},
            timeout=TIMEOUT,
        ) as resp:
            try:
                body = await resp.json(content_type=None)
            except ValueError:
                return False
    except (aiohttp.ClientError, TimeoutError) as err:
        raise WialonConnectionError(str(err)) from err
    return body == {"error": ERROR_INVALID_SESSION}


async def probe_api(session: aiohttp.ClientSession, api_origin: str) -> bool:
    """Return True if api_origin answers like a Wialon API."""
    try:
        return await _probe(session, api_origin)
    except Exception:  # noqa: BLE001
        return False


async def resolve_api_url(session: aiohttp.ClientSession, web_url: str) -> str:
    """Find the API origin for a Wialon web address."""
    web_origin = normalize_origin(web_url)
    connection_failed = False
    try:
        async with session.get(web_origin, timeout=TIMEOUT) as resp:
            html = await resp.text(errors="replace")
        match = _SDK_URL_RE.search(html)
        if match:
            candidate = normalize_origin(match.group(1).replace("\\/", "/"))
            if await probe_api(session, candidate):
                return candidate
    except (aiohttp.ClientError, TimeoutError):
        connection_failed = True
    except ValueError:
        pass

    try:
        if await _probe(session, web_origin):
            return web_origin
    except WialonConnectionError:
        if connection_failed:
            raise
    raise WialonNotWialonError(f"{web_origin} is not a Wialon server")


def build_login_url(web_origin: str, redirect_uri: str) -> str:
    """Build the OAuth-style login page URL (read-only token, never expires)."""
    query = {
        "client_id": "Home Assistant",
        "access_type": "768",  # 0x100 online tracking | 0x200 view; never -1
        "activation_time": "0",
        "duration": "0",
        "flags": "1",
        "redirect_uri": redirect_uri,
        "response_type": "token",
    }
    qs = "&".join(f"{k}={quote(v, safe='')}" for k, v in query.items())
    return f"{normalize_origin(web_origin)}/login.html?{qs}"


class WialonClient:
    """Wialon Remote API client bound to one token."""

    def __init__(
        self, session: aiohttp.ClientSession, api_origin: str, token: str
    ) -> None:
        self._session = session
        self._api = normalize_origin(api_origin)
        self._token = token
        self._sid: str | None = None
        self._lock = asyncio.Lock()

    async def _post(self, svc: str, params: Any, sid: str | None) -> Any:
        data = {"svc": svc, "params": json.dumps(params, separators=(",", ":"))}
        if sid:
            data["sid"] = sid
        try:
            async with self._session.post(
                _ajax_url(self._api), data=data, timeout=TIMEOUT
            ) as resp:
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise WialonConnectionError(f"{svc}: {type(err).__name__}") from err

    async def _login_locked(self) -> str:
        result = await self._post("token/login", {"token": self._token}, None)
        if isinstance(result, dict) and "error" in result:
            code = result["error"]
            if code in AUTH_ERROR_CODES:
                raise WialonAuthError("Token rejected", code)
            raise WialonError("Login failed", code)
        if not isinstance(result, dict) or "eid" not in result:
            raise WialonConnectionError("Unexpected login response")
        self._sid = result["eid"]
        return str(result.get("au", ""))

    @staticmethod
    def _expired(result: Any) -> bool:
        if isinstance(result, dict):
            return result.get("error") == ERROR_INVALID_SESSION
        if isinstance(result, list):
            return any(
                isinstance(r, dict) and r.get("error") == ERROR_INVALID_SESSION
                for r in result
            )
        return False

    async def login(self) -> str:
        """Log in with the token; return the Wialon user name."""
        async with self._lock:
            return await self._login_locked()

    async def logout(self) -> None:
        """Best-effort logout."""
        async with self._lock:
            if not self._sid:
                return
            try:
                await self._post("core/logout", {}, self._sid)
            except WialonError:
                pass
            finally:
                self._sid = None

    async def _call_locked(self, svc: str, params: Any) -> Any:
        """One call with relogin on an expired session; the lock must be held."""
        if self._sid is None:
            await self._login_locked()
        result = await self._post(svc, params, self._sid)
        if self._expired(result):
            _LOGGER.debug("Wialon session expired, logging in again")
            await self._login_locked()
            result = await self._post(svc, params, self._sid)
        if isinstance(result, dict) and "error" in result:
            raise WialonError(f"{svc} failed", result["error"])
        return result

    async def _call(self, svc: str, params: Any) -> Any:
        async with self._lock:
            return await self._call_locked(svc, params)

    async def load_messages(
        self, unit_id: int, time_from: int, time_to: int
    ) -> list[dict]:
        """Load the data messages of a unit in [time_from, time_to] (UTC seconds).

        The loaded message layer lives in the session, so load and unload run
        under one lock hold; a concurrent poll never sees the half state.
        """
        async with self._lock:
            result = await self._call_locked(
                "messages/load_interval",
                {
                    "itemId": int(unit_id),
                    "timeFrom": int(time_from),
                    "timeTo": int(time_to),
                    "flags": 0,
                    "flagsMask": 0xFF00,
                    "loadCount": 0xFFFFFFFF,
                },
            )
            messages = result.get("messages") if isinstance(result, dict) else None
            out = list(messages) if isinstance(messages, list) else []
            try:
                await self._call_locked("messages/unload", {})
            except WialonError as err:
                _LOGGER.debug("messages/unload failed: %s", err)
            return out

    async def list_units(self) -> list[dict]:
        """Return raw avl_unit items visible to the token."""
        result = await self._call(
            "core/search_items",
            {
                "spec": {
                    "itemsType": "avl_unit",
                    "propName": "sys_name",
                    "propValueMask": "*",
                    "sortType": "sys_name",
                },
                "force": 1,
                "flags": UNIT_FLAGS,
                "from": 0,
                "to": 0,
            },
        )
        return list(result.get("items", []))

    async def get_units(self, ids: list[int]) -> dict[int, dict | None]:
        """Fetch units in one batch; failed units map to None."""
        if not ids:
            return {}
        result = await self._call(
            "core/batch",
            {
                "params": [
                    {
                        "svc": "core/search_item",
                        "params": {"id": int(i), "flags": POLL_FLAGS},
                    }
                    for i in ids
                ],
                "flags": 0,
            },
        )
        if not isinstance(result, list) or len(result) != len(ids):
            raise WialonError("Unexpected batch response")
        out: dict[int, dict | None] = {}
        for unit_id, item in zip(ids, result, strict=True):
            if isinstance(item, dict) and "error" not in item:
                out[int(unit_id)] = item.get("item")
            else:
                out[int(unit_id)] = None
        return out


def parse_position(lmsg: dict | None) -> dict | None:
    """Extract `pos` from a last message (or a unit item), or None."""
    if not isinstance(lmsg, dict):
        return None
    pos = lmsg.get("pos")
    if not isinstance(pos, dict) or pos.get("y") is None or pos.get("x") is None:
        return None
    return {
        "latitude": pos["y"],
        "longitude": pos["x"],
        "altitude": pos.get("z"),
        "speed": pos.get("s"),
        "course": pos.get("c"),
        "satellites": pos.get("sc"),
    }


def parse_params(lmsg: dict | None) -> dict:
    """Return the raw device parameters of a last message."""
    if not isinstance(lmsg, dict):
        return {}
    params = lmsg.get("p")
    return dict(params) if isinstance(params, dict) else {}


def _to_datetime(t: Any) -> datetime | None:
    """Convert Wialon UTC seconds to an aware datetime, None when unusable."""
    if isinstance(t, bool) or not isinstance(t, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(t, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def message_time(lmsg: dict | None) -> datetime | None:
    """Return the message time as an aware UTC datetime."""
    if not isinstance(lmsg, dict):
        return None
    return _to_datetime(lmsg.get("t"))


# Structured store keys already logged, so the store can be parsed on every
# state read without repeating the message.
_skipped_structured: set[str] = set()


@dataclass(frozen=True)
class ParamValue:
    """One device parameter with the times Wialon last saw it."""

    value: Any
    changed: datetime | None
    reported: datetime | None


def parse_param_store(item: dict | None) -> dict[str, ParamValue]:
    """Return the accumulated parameters of a unit item.

    Source is `prms` (flag 0x00100000), which keeps a parameter after a message
    omits it. Parameters of the last message that the store lacks are added;
    without `prms` the last message alone is used, stamped with its time.
    """
    if not isinstance(item, dict):
        return {}
    lmsg = item.get("lmsg")
    msg_time = message_time(lmsg)
    out: dict[str, ParamValue] = {}
    prms = item.get("prms")
    if isinstance(prms, dict):
        for key, entry in prms.items():
            if not isinstance(entry, dict) or "v" not in entry:
                continue
            # Structured values cannot be a sensor state: `posinfo` holds the
            # last position, which the device tracker already covers.
            if isinstance(entry["v"], (dict, list)):
                if key not in _skipped_structured:
                    _skipped_structured.add(key)
                    _LOGGER.debug("Skipping structured parameter %s", key)
                continue
            out[key] = ParamValue(
                entry["v"],
                _to_datetime(entry.get("ct")),
                _to_datetime(entry.get("at")),
            )
    for key, value in parse_params(lmsg).items():
        out.setdefault(key, ParamValue(value, msg_time, msg_time))
    return out


def param_attributes(param: ParamValue | None) -> dict[str, str | None]:
    """State attributes of a parameter.

    Only the change time: the report time moves with every message, so as an
    attribute it would write a state row per poll even when nothing changed.
    """
    changed = param.changed if param else None
    return {"last_changed": changed.isoformat() if changed else None}
