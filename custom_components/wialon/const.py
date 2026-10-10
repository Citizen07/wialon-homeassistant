"""Constants for the Wialon integration."""

from __future__ import annotations

DOMAIN = "wialon"

CONF_WEB_URL = "web_url"
CONF_API_URL = "api_url"
CONF_TOKEN = "token"
CONF_USER_NAME = "user_name"
CONF_UNITS = "units"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 3600

DEFAULT_WEB_URLS = [
    "https://hosting.wialon.com",
    "https://hosting.wialon.eu",
    "https://gps.sensetracking.com",
]

# Raw device parameter keys (lmsg["p"]) whose sensors are enabled by default.
# Provisional until a real unit's parameters are known; see diagnostics.
DEFAULT_ENABLED_PARAMS = {
    "pwr_ext",
    "pwr_int",
    "ign",
    "mileage",
    "can_mileage",
    "fuel_level",
}

OAUTH_VIEW_REGISTERED = "wialon_oauth_view_registered"
OAUTH_URL_BASE = "/api/wialon/oauth"
