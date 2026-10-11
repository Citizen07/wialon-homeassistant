"""Tests for the Xirgo parameter conversion (pure, no Home Assistant)."""

from __future__ import annotations

import pytest

# key -> (multiplier, offset, unit) that the Xirgo table itself must hold.
XIRGO_EXPECTED = {
    "can_taho": (0.125, 0, "rpm"),
    "engine_temp": (1, -40, "°C"),
    "j1939_air_temp": (0.03125, -273, "°C"),
    "j1939_fuel_rate": (0.05, 0, "L/h"),
    "j1939_flfmseco": (0.001953125, 0, "km/L"),
    "total_fuel": (0.5, 0, "L"),
    "j1939_fl_con": (0.001, 0, "L"),
    "can_engine_hrs": (0.05, 0, "h"),
    "can_mileage": (0.005, 0, "km"),
    "j1939_adblue": (0.4, 0, "%"),
    "acc_pedal": (0.4, 0, "%"),
    "j1939_eng_load": (1, 0, "%"),
    "axle1": (0.5, 0, "kg"),
    "axle2": (0.5, 0, "kg"),
    "axle3": (0.5, 0, "kg"),
    "axle4": (0.5, 0, "kg"),
    "axle5": (0.5, 0, "kg"),
    "srv_dist": (5, -160635, "km"),
    "j1979_rpm": (0.25, 0, "rpm"),
    "j1979_flrate": (0.05, 0, "L/h"),
    "j1979_maf": (0.01, 0, "g/s"),
}


def test_param_map_ids_exist(params, xirgo):
    for key, spec in params.PARAM_MAP.items():
        if spec.source == "wialon":
            assert spec.sensor_id is None, key
            continue
        assert spec.sensor_id in xirgo.XIRGO_SENSORS, key


@pytest.mark.parametrize("key", sorted(XIRGO_EXPECTED))
def test_xirgo_table_values(params, xirgo, key):
    mult, off, unit = XIRGO_EXPECTED[key]
    row = xirgo.XIRGO_SENSORS[params.PARAM_MAP[key].sensor_id]
    assert (row.multiplier, row.offset, row.unit) == (mult, off, unit)


def test_user_defined_slots_have_no_xirgo_scale(params, xirgo):
    for key, spec in params.PARAM_MAP.items():
        if spec.source == "sae_j1979":
            row = xirgo.XIRGO_SENSORS[spec.sensor_id]
            assert row.multiplier is None, key
            assert row.alt_name and row.alt_name.startswith("Sensor_J1979_"), key
            assert spec.multiplier is not None, key


def test_xirgo_table_size(xirgo):
    assert len(xirgo.XIRGO_SENSORS) > 400


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("j1939_adblue", 231, 92.4),
        ("can_taho", 30532, 3816.5),
        ("engine_temp", 241, 201),
        ("can_mileage", 2500000, 12500.0),
        ("can_engine_hrs", 319951120, 15997556.0),
        ("j1939_air_temp", 60653, 60653 * 0.03125 - 273),
        ("j1979_speed", 78, 78),
        ("j1979_eng_temp", 70, 30),
        ("j1979_fl_lvl", 255, 100.0),
        ("srv_dist", 32127, 0),
    ],
)
def test_convert(params, key, raw, expected):
    assert params.convert(params.resolve(key), raw) == pytest.approx(expected)


def test_air_temp_value(params):
    assert params.convert(params.resolve("j1939_air_temp"), 60653) == 1622.40625


@pytest.mark.parametrize("raw", ["12", None, True, False, [1], {"a": 1}])
def test_convert_non_numeric(params, raw):
    assert params.convert(params.resolve("can_taho"), raw) is None


def test_unmapped_key(params):
    assert params.resolve("pwr_ext") is None


def test_overrides_win(params):
    res = params.resolve("j1979_speed")
    assert (res.multiplier, res.offset, res.unit) == (1.0, 0.0, "km/h")
    assert res.device_class == "speed"


def test_defaults_when_no_scale(params, monkeypatch):
    spec = params.ParamSpec(8219)  # user-defined slot without Xirgo scale
    monkeypatch.setitem(params.PARAM_MAP, "t", spec)
    res = params.resolve("t")
    assert (res.multiplier, res.offset, res.unit) == (1.0, 0.0, None)
    assert res.precision == 0
    assert res.state_class == "measurement"


@pytest.mark.parametrize(
    ("key", "name"),
    [
        ("can_taho", "Engine speed"),
        ("j1939_adblue", "AdBlue level"),
        ("j1979_speed", "J1979 speed"),
        ("can_engine_hrs", "Total engine hours"),
        ("j1979_rpm", "J1979 RPM"),
        ("j1979_eng_temp", "J1979 engine temp"),
        ("j1979_maf", "J1979 MAF"),
        ("axle3", "Axle 3 weight"),
    ],
)
def test_names(params, key, name):
    assert params.resolve(key).name == name


@pytest.mark.parametrize(
    ("key", "precision"),
    [
        ("j1939_eng_load", 0),
        ("engine_temp", 0),
        ("srv_dist", 0),
        ("total_fuel", 1),
        ("j1939_adblue", 1),
        ("j1939_fuel_rate", 2),
        ("j1939_air_temp", 2),
        ("j1939_fl_con", 3),
        ("j1939_flfmseco", 3),
        ("j1979_fl_lvl", 1),
    ],
)
def test_precision(params, key, precision):
    assert params.resolve(key).precision == precision


def test_all_mapped_keys_resolve_with_valid_classes(params):
    for key in params.PARAM_MAP:
        res = params.resolve(key)
        assert res is not None and res.name, key
        assert res.state_class in ("measurement", "total_increasing")


SAMPLE_SENSORS = """\
---
title: x
---
| ID | Name | Reset value | Multiplier | Offset | Unit | Update period, ms | set_val |
| --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |
| 0 | - | 0 | 1 | 0 | - | - | Inadvisable |
| **Section** |  |  |  |  |  |  |  |
| 1 | SENSOR_MODEM_ON | 0 | 1 | 0 | - | 100 | No |
| 12344 | SENSOR_SERVICE_DISTANCE | 0 | 5 | -160635 | km | on CAN RX | No |
| 12300 | SENSOR_ENGINE_SPEED | 0 | 0.125 | 0 | rpm | on CAN RX | No |
| 8219 | SENSOR_U8_USER_DEFINED_16 | 0 | - | - | - | on set_val | Yes |
| 99 | SENSOR_ODD | 0 | abc | - | V | 1 | No |
"""

SAMPLE_LEGACY = """\
| ID | Name | Alternative Names 1 | Alternative Names 2 |
| --- | --- | --- | --- |
|  |  |  |  |
| 0 | - | - | Sensor_SignalReset |
| 1 | SENSOR_MODEM_ON | - | Sensor_SignalModemOn |
| 8219 | SENSOR_U8_USER_DEFINED_16 | Sensor_J1979_Speed | Sensor_U8UserDefined16 |
"""


def test_parser(gen_script):
    table = gen_script.build_table(SAMPLE_SENSORS, SAMPLE_LEGACY)
    assert sorted(table) == [1, 99, 8219, 12300, 12344]
    assert table[1] == ("SENSOR_MODEM_ON", None, 1.0, 0.0, None)
    assert table[12344] == ("SENSOR_SERVICE_DISTANCE", None, 5.0, -160635.0, "km")
    assert table[12300] == ("SENSOR_ENGINE_SPEED", None, 0.125, 0.0, "rpm")
    assert table[8219] == (
        "SENSOR_U8_USER_DEFINED_16",
        "Sensor_J1979_Speed",
        None,
        None,
        None,
    )
    assert table[99][2] is None  # non-numeric multiplier


def test_render_is_importable_source(gen_script):
    from datetime import date

    table = gen_script.build_table(SAMPLE_SENSORS, SAMPLE_LEGACY)
    src = gen_script.render(table, date(2026, 1, 2))
    assert "do not edit" in src and "2026-01-02" in src
    ns: dict = {}
    exec(compile(src, "xirgo_sensors.py", "exec"), ns)  # noqa: S102
    assert ns["XIRGO_SENSORS"][12300].multiplier == 0.125


def test_precision_overrides(params):
    # rpm and odometer would otherwise show 3 decimals from their multipliers.
    assert params.resolve("can_taho").precision == 0
    assert params.resolve("j1979_rpm").precision == 0
    assert params.resolve("can_mileage").precision == 1


def test_generic_distance_total(params):
    res = params.resolve("sensor16387")
    assert params.convert(res, 49937800) == pytest.approx(249689.0)
    assert (res.unit, res.device_class, res.state_class) == (
        "km",
        "distance",
        "total_increasing",
    )
    assert res.enabled_default and not res.diagnostic and not res.is_binary


def test_generic_engine_temp(params):
    res = params.resolve("sensor8200")
    assert params.convert(res, 40) == 0
    assert (res.unit, res.device_class) == ("°C", "temperature")


def test_generic_hdop_diagnostic(params):
    res = params.resolve("sensor8194")
    assert params.convert(res, 6) == pytest.approx(0.6)
    assert res.diagnostic and res.name == "GNSS HDOP"


def test_generic_voltage(params):
    res = params.resolve("sensor12288")
    assert (res.unit, res.device_class, res.enabled_default) == ("mV", "voltage", True)


def test_generic_duplicate_of_fixed_sensor(params):
    for sensor_id in (8192, 8193, 12299, 28675):
        assert not params.resolve(f"sensor{sensor_id}").enabled_default


def test_generic_user_defined(params):
    res = params.resolve("sensor28676")
    assert not res.enabled_default
    assert (res.multiplier, res.offset) == (1.0, 0.0)


def test_generic_timestamp(params):
    res = params.resolve("sensor16492")
    assert res.unit == "s" and res.state_class is None
    assert not res.enabled_default and res.diagnostic


def test_generic_string_sensor_is_diagnostic_and_off(params):
    res = params.resolve("sensor49177")
    assert res.diagnostic and not res.enabled_default


def test_binary_lock(params):
    res = params.resolve("sensor97")
    assert res.is_binary and res.binary_device_class == "lock"
    assert res.invert and res.enabled_default


def test_binary_door_light_diag(params):
    door = params.resolve("sensor98")
    assert door.binary_device_class == "door" and door.name == "Doors F L"
    light = params.resolve("sensor135")
    assert light.binary_device_class == "light" and not light.enabled_default
    seven = params.resolve("sensor7")
    assert seven.is_binary and seven.diagnostic and not seven.enabled_default


@pytest.mark.parametrize("key", ["sensor99999", "hdop", "sensor", "sensorx1"])
def test_generic_unresolved(params, key):
    assert params.resolve(key) is None


def test_param_map_not_binary_or_generic(params):
    for key in params.PARAM_MAP:
        res = params.resolve(key)
        assert not res.is_binary and res.enabled_default and not res.diagnostic


def test_gsm_power_is_negative(params):
    res = params.resolve("sensor8197")
    assert res.unit == "dBm"
    assert params.convert(res, 65) == -65


@pytest.mark.parametrize("key", ["engine_temp", "j1979_eng_temp", "sensor8200", "srv_dist"])
def test_raw_zero_on_offset_sensor_is_no_reading(params, key):
    res = params.resolve(key)
    assert res.offset != 0
    assert params.convert(res, 0) is None
    assert params.convert(res, 0.0) is None


def test_raw_zero_without_offset_is_a_value(params):
    assert params.convert(params.resolve("sensor16387"), 0) == 0
    assert params.convert(params.resolve("can_taho"), 0) == 0


@pytest.mark.parametrize(
    ("key", "name"),
    [
        ("wln_accel_max", "Eco-driving max acceleration"),
        ("wln_brk_max", "Eco-driving max braking"),
        ("wln_crn_max", "Eco-driving max cornering"),
    ],
)
def test_wialon_eco_driving_standalone(params, key, name):
    res = params.resolve(key)
    assert res.name == name
    assert (res.multiplier, res.offset, res.unit, res.precision) == (1.0, 0.0, "g", 3)
    assert res.state_class == "measurement"
    assert res.enabled_default and not res.is_binary and not res.diagnostic
    assert params.convert(res, 0) == 0
    assert params.convert(res, 0.25) == 0.25


def test_standalone_spec_defaults(params, monkeypatch):
    monkeypatch.setitem(params.PARAM_MAP, "x", params.ParamSpec(None, source="wialon"))
    res = params.resolve("x")
    assert (res.name, res.multiplier, res.offset, res.unit) == ("x", 1.0, 0.0, None)
    assert res.precision == 0
