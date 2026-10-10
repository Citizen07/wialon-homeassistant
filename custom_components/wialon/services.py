"""Services of the Wialon integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import logging
import re
from typing import Any

from homeassistant.components.persistent_notification import (
    async_create as async_create_notification,
)
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    STATISTIC_UNIT_TO_UNIT_CONVERTER,
    async_import_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.components.sensor.const import UNIT_CONVERTERS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.recorder import get_instance

from .api import WialonError, parse_params, parse_position
from .const import CONF_UNITS, DOMAIN
from .history import (
    NO_READING,
    Series,
    chain_sums,
    compact,
    extract_samples,
    hourly_measurement,
    hourly_total,
    rows_before,
)
from .params import convert, resolve

_LOGGER = logging.getLogger(__name__)

SERVICE_IMPORT_HISTORY = "import_history"
ATTR_DEVICE_ID = "device_id"
ATTR_DAYS = "days"
DEFAULT_DAYS = 365
MAX_DAYS = 366
REQUEST_PAUSE = 0.5
LOG_EVERY_DAYS = 30
DATA_IMPORT_RUNNING = f"{DOMAIN}_import_running"
NOTIFICATION_ID = f"{DOMAIN}_import_history"
DAY = 86400

_UID_RE = re.compile(r"^(\d+)_(?:param_(.+)|(speed|course|altitude|satellites))$")
_KINDS = {"measurement": "measurement", "total_increasing": "total"}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _param_extractor(key: str) -> Callable[[dict], float | None]:
    resolved = resolve(key)

    def _get(msg: dict) -> float | None:
        value = parse_params(msg).get(key)
        if not _is_number(value):
            return None
        if resolved is None:
            return float(value)
        converted = convert(resolved, value)
        return NO_READING if converted is None else converted

    return _get


def _pos_extractor(field: str) -> Callable[[dict], float | None]:
    def _get(msg: dict) -> float | None:
        pos = parse_position(msg)
        value = pos.get(field) if pos else None
        return float(value) if _is_number(value) else None

    return _get


def _unit_class(device_class: str | None, unit: str | None) -> str | None:
    """Same choice as the sensor recorder platform makes for live entities."""
    if (
        device_class
        and (conv := UNIT_CONVERTERS.get(device_class))
        and unit in conv.VALID_UNITS
    ):
        return conv.UNIT_CLASS
    if conv := STATISTIC_UNIT_TO_UNIT_CONVERTER.get(unit):
        return conv.UNIT_CLASS
    return None


def _collect_series(
    hass: HomeAssistant, entry_id: str, unit_id: int
) -> tuple[list[tuple[Series, er.RegistryEntry]], list[str]]:
    """Enabled numeric sensor entities of a unit that have a state class."""
    registry = er.async_get(hass)
    found: list[tuple[Series, er.RegistryEntry]] = []
    skipped: list[str] = []
    for reg in er.async_entries_for_config_entry(registry, entry_id):
        if reg.domain != "sensor" or reg.platform != DOMAIN or reg.disabled_by:
            continue
        match = _UID_RE.match(reg.unique_id)
        if not match or int(match.group(1)) != unit_id:
            continue
        kind = _KINDS.get(str((reg.capabilities or {}).get("state_class")))
        if kind is None:
            continue
        native = reg.unit_of_measurement
        state = hass.states.get(reg.entity_id)
        live_unit = state.attributes.get("unit_of_measurement") if state else native
        if live_unit != native:
            skipped.append(
                f"{reg.entity_id}: unit is {live_unit}, Wialon values are in {native}"
            )
            continue
        extractor = (
            _param_extractor(match.group(2))
            if match.group(2)
            else _pos_extractor(match.group(3))
        )
        found.append((Series(reg.entity_id, extractor, kind), reg))
    return found, skipped


def _earliest_rows(
    hass: HomeAssistant, start: datetime, ids: set[str]
) -> dict[str, dict]:
    """Earliest existing hourly statistics row per id since start (recorder thread)."""
    result = statistics_during_period(
        hass, start, None, ids, "hour", None, {"state", "sum"}
    )
    return {
        sid: min(rows, key=lambda r: r["start"])
        for sid, rows in result.items()
        if rows
    }


def _metadata(hass: HomeAssistant, ids: set[str]) -> dict:
    """Existing statistics metadata (recorder thread)."""
    return get_metadata(hass, statistic_ids=ids)


async def _import_unit(
    hass: HomeAssistant,
    client: Any,
    entry_id: str,
    unit_id: int,
    days: int,
    now: datetime,
) -> dict[str, Any]:
    """Import one unit; returns its report."""
    report: dict[str, Any] = {"days": 0, "messages": 0, "rows": {}, "skipped": []}
    series_regs, skipped = _collect_series(hass, entry_id, unit_id)
    report["skipped"] = skipped
    if not series_regs:
        return report

    now_hour = now.replace(minute=0, second=0, microsecond=0)
    start = (now_hour - timedelta(days=days)).replace(hour=0)
    samples: dict[str, list[tuple[float, float]]] = {
        s.statistic_id: [] for s, _ in series_regs
    }
    ids = set(samples)

    day_start = int(start.timestamp())
    end_ts = int(now.timestamp())
    fetched = 0
    while day_start < end_ts:
        day_end = min(day_start + DAY - 1, end_ts)
        messages = await client.load_messages(unit_id, day_start, day_end)
        report["messages"] += len(messages)
        for series, _ in series_regs:
            kept = samples[series.statistic_id]
            kept.extend(
                compact(
                    extract_samples(messages, series.extractor),
                    kept[-1] if kept else None,
                )
            )
        fetched += 1
        if fetched % LOG_EVERY_DAYS == 0:
            _LOGGER.info(
                "Wialon history import, unit %s: %s days fetched, %s messages",
                unit_id,
                fetched,
                report["messages"],
            )
        day_start += DAY
        if day_start < end_ts:
            await asyncio.sleep(REQUEST_PAUSE)
    report["days"] = fetched

    instance = get_instance(hass)
    earliest = await instance.async_add_executor_job(_earliest_rows, hass, start, ids)
    metadata = await instance.async_add_executor_job(_metadata, hass, ids)

    for series, reg in series_regs:
        sid = series.statistic_id
        existing = earliest.get(sid)
        cutoff = (
            datetime.fromtimestamp(existing["start"], UTC) if existing else now_hour
        )
        if series.kind == "total":
            rows = hourly_total(samples[sid], end_ts)
            anchor = None
            if existing:
                if existing.get("sum") is None or existing.get("state") is None:
                    report["skipped"].append(
                        f"{sid}: existing statistics lack sum or state"
                    )
                    continue
                anchor = (existing["sum"], existing["state"])
            rows = chain_sums(rows, anchor)
        else:
            rows = hourly_measurement(samples[sid], end_ts)
        rows = rows_before(rows, cutoff)
        if not rows:
            report["rows"][sid] = 0
            continue
        unit = reg.unit_of_measurement
        old = metadata.get(sid)
        unit_class = (
            old[1]["unit_class"]
            if old
            else _unit_class(reg.device_class or reg.original_device_class, unit)
        )
        meta = {
            "mean_type": (
                StatisticMeanType.ARITHMETIC
                if series.kind == "measurement"
                else StatisticMeanType.NONE
            ),
            "has_sum": series.kind == "total",
            "name": None,
            "source": "recorder",
            "statistic_id": sid,
            "unit_class": unit_class,
            "unit_of_measurement": unit,
        }
        try:
            async_import_statistics(hass, meta, rows)
        except HomeAssistantError as err:
            report["skipped"].append(f"{sid}: {err}")
            continue
        report["rows"][sid] = len(rows)
    return report


def _format_report(reports: dict[str, dict[str, Any]], errors: dict[str, str]) -> str:
    lines: list[str] = []
    for name, rep in reports.items():
        lines.append(
            f"**{name}**: {rep['days']} days, {rep['messages']} messages, "
            f"{sum(rep['rows'].values())} statistics rows"
        )
        lines.extend(f"- {sid}: {count} rows" for sid, count in rep["rows"].items())
        lines.extend(f"- skipped {note}" for note in rep["skipped"])
    lines.extend(f"**{name}**: failed: {err}" for name, err in errors.items())
    return "\n".join(lines) or "Nothing to import."


def _targets(hass: HomeAssistant, device_ids: list[str] | None) -> list:
    """List of (entry, unit_id, display name)."""
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED
    ]
    if not entries:
        raise ServiceValidationError("No loaded Wialon config entry")
    targets = []
    if device_ids:
        dev_reg = dr.async_get(hass)
        for device_id in device_ids:
            device = dev_reg.async_get(device_id)
            ident = next(
                (i[1] for i in (device.identifiers if device else ()) if i[0] == DOMAIN),
                None,
            )
            entry = next(
                (e for e in entries if device and e.entry_id in device.config_entries),
                None,
            )
            if ident is None or entry is None or not str(ident).isdigit():
                raise ServiceValidationError(
                    f"Device {device_id} is not a loaded Wialon unit"
                )
            name = device.name_by_user or device.name or str(ident)
            targets.append((entry, int(ident), name))
    else:
        for entry in entries:
            for unit in entry.options.get(CONF_UNITS, []):
                targets.append((entry, int(unit), str(unit)))
    if not targets:
        raise ServiceValidationError("No Wialon units selected")
    return targets


async def _run_import(hass: HomeAssistant, targets: list, days: int) -> None:
    reports: dict[str, dict] = {}
    errors: dict[str, str] = {}
    try:
        for entry, unit_id, name in targets:
            _LOGGER.info("Wialon history import: starting %s (%s days)", name, days)
            try:
                reports[name] = await _import_unit(
                    hass,
                    entry.runtime_data.client,
                    entry.entry_id,
                    unit_id,
                    days,
                    datetime.now(UTC),
                )
            except (WialonError, HomeAssistantError) as err:
                _LOGGER.warning("Wialon history import of %s failed: %s", name, err)
                errors[name] = str(err)
        message = _format_report(reports, errors)
    except Exception as err:  # noqa: BLE001 - tell the user whatever happened
        _LOGGER.exception("Wialon history import crashed")
        message = f"Import failed: {err}"
    finally:
        hass.data[DATA_IMPORT_RUNNING] = False
    async_create_notification(
        hass, message, title="Wialon history import", notification_id=NOTIFICATION_ID
    )
    _LOGGER.info("Wialon history import finished")


async def _async_import_history(call: ServiceCall) -> None:
    hass = call.hass
    if hass.data.get(DATA_IMPORT_RUNNING):
        raise ServiceValidationError("A Wialon history import is already running")
    try:
        days = int(call.data.get(ATTR_DAYS, DEFAULT_DAYS))
    except (TypeError, ValueError):
        raise ServiceValidationError("days must be a whole number") from None
    if not 1 <= days <= MAX_DAYS:
        raise ServiceValidationError(f"days must be between 1 and {MAX_DAYS}")
    device_ids = call.data.get(ATTR_DEVICE_ID)
    if isinstance(device_ids, str):
        device_ids = [device_ids]
    targets = _targets(hass, device_ids)
    hass.data[DATA_IMPORT_RUNNING] = True
    hass.async_create_background_task(
        _run_import(hass, targets, days), name="wialon_import_history"
    )


def async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's services."""
    hass.services.async_register(DOMAIN, SERVICE_IMPORT_HISTORY, _async_import_history)
