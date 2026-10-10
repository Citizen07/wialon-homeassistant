"""Tests for custom_components/wialon/api.py against a fake Wialon server."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from urllib.parse import parse_qs, urlsplit

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

TOKEN = "T" * 72


def unit(uid: int, **extra):
    item = {"id": uid, "nm": f"Unit {uid}", "uid": f"35000{uid}", "hw": 1}
    item.update(extra)
    return item


class FakeWialon:
    """Fake of the Wialon Remote API plus an optional login page."""

    def __init__(self) -> None:
        self.token = TOKEN
        self.login_error: int | None = None
        self.sid = None
        self.sid_counter = 0
        self.logins = 0
        self.logouts = 0
        self.calls: list[tuple[str, dict, str | None]] = []
        self.units = {1: unit(1), 2: unit(2, lmsg=None)}
        self.batch_element_expired_once = False
        self.html: str | None = None
        self.api_enabled = True
        self.raw_body: str | None = None
        self.always_expired = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/wialon/ajax.html", self.ajax)
        app.router.add_get("/", self.index)
        return app

    async def index(self, request):
        if self.html is None:
            return web.Response(status=404, text="not found")
        return web.Response(text=self.html, content_type="text/html")

    def expire(self) -> None:
        self.sid = None

    async def ajax(self, request):
        if not self.api_enabled:
            return web.Response(status=404, text="nope")
        if self.raw_body is not None:
            return web.Response(text=self.raw_body, content_type="text/html")
        if request.method == "GET":
            return web.json_response({"error": 1})
        form = await request.post()
        svc = form["svc"]
        params = json.loads(form["params"])
        sid = form.get("sid")
        self.calls.append((svc, params, sid))
        if svc == "token/login":
            if self.login_error is not None:
                return web.json_response({"error": self.login_error})
            if params["token"] != self.token:
                return web.json_response({"error": 4})
            self.logins += 1
            self.sid_counter += 1
            self.sid = f"sid{self.sid_counter}"
            return web.json_response({"eid": self.sid, "au": "alice", "user": {}})
        if self.always_expired or sid is None or sid != self.sid:
            return web.json_response({"error": 1})
        if svc == "core/logout":
            self.logouts += 1
            self.sid = None
            return web.json_response({})
        if svc == "core/search_items":
            return web.json_response({"items": list(self.units.values())})
        if svc == "core/search_item":
            return web.json_response(self._item(params["id"]))
        if svc == "core/batch":
            out = [self._item(c["params"]["id"]) for c in params["params"]]
            if self.batch_element_expired_once:
                self.batch_element_expired_once = False
                out[0] = {"error": 1}
            return web.json_response(out)
        return web.json_response({"error": 3})

    def _item(self, uid):
        if uid in self.units:
            return {"item": self.units[uid]}
        return {"error": 6}


async def _serve(fake: FakeWialon) -> TestServer:
    server = TestServer(fake.app())
    await server.start_server()
    return server


def origin(server: TestServer) -> str:
    return f"http://127.0.0.1:{server.port}"


@pytest_asyncio.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


@pytest_asyncio.fixture
async def fake():
    f = FakeWialon()
    server = await _serve(f)
    f.server = server
    f.origin = origin(server)
    yield f
    await server.close()


@pytest_asyncio.fixture
async def client(api, session, fake):
    return api.WialonClient(session, fake.origin, TOKEN)


# --- normalize_origin ---------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hosting.wialon.com", "https://hosting.wialon.com"),
        ("https://Hosting.Wialon.EU/", "https://hosting.wialon.eu"),
        ("https://gps.example.com/some/path?x=1#frag", "https://gps.example.com"),
        ("http://10.0.0.5:8022/wialon/ajax.html", "http://10.0.0.5:8022"),
        ("  gps.example.com:8443/path ", "https://gps.example.com:8443"),
    ],
)
async def test_normalize_origin(api, raw, expected):
    assert api.normalize_origin(raw) == expected


async def test_normalize_origin_invalid(api):
    with pytest.raises(ValueError):
        api.normalize_origin("https://")


# --- build_login_url ----------------------------------------------------


async def test_build_login_url(api):
    redirect = "https://ha.local:8123/api/wialon/oauth/abc123"
    url = api.build_login_url("https://Hosting.wialon.com/", redirect)
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://hosting.wialon.com/login.html"
    )
    q = parse_qs(parts.query, keep_blank_values=True)
    assert q["access_type"] == ["768"]
    assert q["duration"] == ["0"]
    assert q["activation_time"] == ["0"]
    assert q["flags"] == ["1"]
    assert q["response_type"] == ["token"]
    assert q["client_id"] == ["Home Assistant"]
    assert q["redirect_uri"] == [redirect]
    assert "-1" not in q["access_type"]
    # redirect_uri is fully encoded in the raw query
    assert "redirect_uri=https%3A%2F%2Fha.local%3A8123%2Fapi%2Fwialon%2Foauth%2Fabc123" in url


# --- parse helpers ------------------------------------------------------


async def test_parse_helpers(api):
    lmsg = {
        "t": 1700000000,
        "pos": {"y": 54.9, "x": 23.9, "z": 70, "s": 12, "c": 180, "sc": 9},
        "p": {"pwr_ext": 12.5, "fw_version": "1.2"},
    }
    assert api.parse_position(lmsg) == {
        "latitude": 54.9,
        "longitude": 23.9,
        "altitude": 70,
        "speed": 12,
        "course": 180,
        "satellites": 9,
    }
    assert api.parse_params(lmsg) == {"pwr_ext": 12.5, "fw_version": "1.2"}
    assert api.message_time(lmsg) == datetime.fromtimestamp(1700000000, UTC)
    assert api.message_time(lmsg).tzinfo is not None


async def test_parse_helpers_missing(api):
    assert api.parse_position(None) is None
    assert api.parse_position({"t": 1, "pos": None}) is None
    assert api.parse_position({"t": 1}) is None
    assert api.parse_params(None) == {}
    assert api.parse_params({"t": 1, "p": None}) == {}
    assert api.message_time(None) is None
    assert api.message_time({"pos": None}) is None


# --- resolve / probe ------------------------------------------------------


async def test_probe(api, session, fake):
    assert await api.probe_api(session, fake.origin) is True
    fake.raw_body = '{"error": 4}'
    assert await api.probe_api(session, fake.origin) is False
    fake.raw_body = "<html>hello</html>"
    assert await api.probe_api(session, fake.origin) is False
    fake.raw_body = None
    fake.api_enabled = False
    assert await api.probe_api(session, fake.origin) is False


async def test_probe_connection_refused(api, session):
    assert await api.probe_api(session, "http://127.0.0.1:9") is False


async def test_resolve_via_sdk_url(api, session, fake):
    web_fake = FakeWialon()
    web_fake.api_enabled = False  # like hosting.wialon.com: no API on the web host
    api_escaped = fake.origin.replace("/", "\\/")
    web_fake.html = (
        '<script>var API_URL="https:\\/\\/recommender.wialon.com";'
        f'var api_url="{api_escaped}"; sdk_url = "{api_escaped}";</script>'
    )
    web_server = await _serve(web_fake)
    try:
        resolved = await api.resolve_api_url(session, origin(web_server) + "/")
        assert resolved == fake.origin
    finally:
        await web_server.close()


async def test_resolve_wialon_local_same_host(api, session, fake):
    fake.html = "<html>login page without sdk url</html>"
    assert await api.resolve_api_url(session, fake.origin) == fake.origin


async def test_resolve_sdk_url_dead_falls_back_to_same_host(api, session, fake):
    fake.html = "sdk_url='http:\\/\\/127.0.0.1:9'"
    assert await api.resolve_api_url(session, fake.origin) == fake.origin


async def test_resolve_not_wialon(api, session):
    other = FakeWialon()
    other.api_enabled = False
    other.html = "<html>some other site</html>"
    server = await _serve(other)
    try:
        with pytest.raises(api.WialonNotWialonError):
            await api.resolve_api_url(session, origin(server))
    finally:
        await server.close()


async def test_resolve_other_json(api, session, fake):
    fake.raw_body = '{"hello": "world"}'
    with pytest.raises(api.WialonNotWialonError):
        await api.resolve_api_url(session, fake.origin)


async def test_resolve_unreachable(api, session):
    with pytest.raises(api.WialonConnectionError):
        await api.resolve_api_url(session, "http://127.0.0.1:9")


# --- client ---------------------------------------------------------------


async def test_login_success(api, client, fake):
    assert await client.login() == "alice"
    svc, params, sid = fake.calls[0]
    assert svc == "token/login"
    assert params == {"token": TOKEN}
    assert sid is None


@pytest.mark.parametrize("code", [4, 7, 8])
async def test_login_auth_errors(api, client, fake, code):
    fake.login_error = code
    with pytest.raises(api.WialonAuthError) as err:
        await client.login()
    assert err.value.code == code


async def test_login_other_error_is_not_auth(api, client, fake):
    fake.login_error = 5
    with pytest.raises(api.WialonError) as err:
        await client.login()
    assert not isinstance(err.value, api.WialonAuthError)
    assert err.value.code == 5


async def test_bad_token(api, session, fake):
    c = api.WialonClient(session, fake.origin, "wrong")
    with pytest.raises(api.WialonAuthError):
        await c.login()


async def test_list_units(api, client, fake):
    units = await client.list_units()
    assert [u["id"] for u in units] == [1, 2]
    svc, params, sid = fake.calls[-1]
    assert svc == "core/search_items"
    assert params["flags"] == 1281
    assert params["spec"]["itemsType"] == "avl_unit"
    assert sid == "sid1"
    # token is only ever sent to token/login
    assert all(c[0] == "token/login" or TOKEN not in json.dumps(c[1]) for c in fake.calls)


async def test_get_units_with_removed_unit(api, client, fake):
    result = await client.get_units([1, 99, 2])
    assert result[1]["id"] == 1
    assert result[99] is None
    assert result[2]["id"] == 2
    batch = [c for c in fake.calls if c[0] == "core/batch"]
    assert len(batch) == 1
    inner = batch[0][1]["params"]
    assert [c["svc"] for c in inner] == ["core/search_item"] * 3
    assert inner[0]["params"] == {"id": 1, "flags": 1281}


async def test_get_units_empty(api, client, fake):
    assert await client.get_units([]) == {}
    assert fake.calls == []


async def test_session_expiry_relogin_once(api, client, fake):
    await client.list_units()
    assert fake.logins == 1
    fake.expire()
    units = await client.list_units()
    assert len(units) == 2
    assert fake.logins == 2


async def test_batch_element_expired_triggers_relogin(api, client, fake):
    await client.login()
    fake.batch_element_expired_once = True
    result = await client.get_units([1, 2])
    assert result[1]["id"] == 1
    assert fake.logins == 2


async def test_concurrent_calls_relogin_once(api, client, fake):
    await client.list_units()
    fake.expire()
    results = await asyncio.gather(*(client.list_units() for _ in range(4)))
    assert all(len(r) == 2 for r in results)
    assert fake.logins == 2


async def test_relogin_auth_failure(api, client, fake):
    await client.list_units()
    fake.expire()
    fake.token = "revoked"
    with pytest.raises(api.WialonAuthError):
        await client.list_units()


async def test_persistent_error_one_raises(api, client, fake):
    fake.always_expired = True
    with pytest.raises(api.WialonError) as err:
        await client.list_units()
    assert err.value.code == 1
    assert fake.logins == 2  # initial login plus exactly one retry


async def test_logout(api, client, fake):
    await client.list_units()
    await client.logout()
    assert fake.logouts == 1
    await client.logout()  # no session: no call, no error
    assert fake.logouts == 1


async def test_logout_ignores_errors(api, session, fake):
    c = api.WialonClient(session, fake.origin, TOKEN)
    await c.login()
    await fake.server.close()
    await c.logout()  # must not raise


async def test_connection_error(api, session):
    c = api.WialonClient(session, "http://127.0.0.1:9", TOKEN)
    with pytest.raises(api.WialonConnectionError):
        await c.login()


async def test_non_json_response(api, client, fake):
    fake.raw_body = "<html>maintenance</html>"
    with pytest.raises(api.WialonConnectionError):
        await client.login()


async def test_parse_position_from_unit_item(api):
    # Unit items carry the last known position at top level (flag 0x400).
    item = {"lmsg": {"t": 2, "pos": None}, "pos": {"y": 54.9, "x": 23.9, "sc": 0}}
    assert api.parse_position(item["lmsg"]) is None
    assert api.parse_position(item)["latitude"] == 54.9
