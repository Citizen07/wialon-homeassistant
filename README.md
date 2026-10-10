# Wialon for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration that brings units (vehicles and other
trackers) from a Wialon account into Home Assistant: GPS position, motion data
and the parameters reported by the tracker, including Xirgo XG devices.

## Features

- Presets for `hosting.wialon.com`, `hosting.wialon.eu` and
  `gps.sensetracking.com`; any other Wialon web address, including Wialon
  Local, works too.
- Sign in through the Wialon login page, or paste a read-only access token.
- Choose which units to track; change the selection and the poll interval later.
- GPS device tracker, speed, course, altitude, satellites and last message
  sensors for each unit.
- Xirgo XG parameters converted to readable sensors and binary sensors with
  names, units, multipliers and offsets from the Xirgo sensor table.
- `wialon.import_history` action to backfill long-term statistics.
- Reauthentication prompt when the token is revoked.
- Diagnostics download with the token redacted.

## Requirements

- Home Assistant 2026.3 or newer.
- A Wialon account (Wialon Hosting or Wialon Local).

## Installation

### HACS

1. In HACS, open the menu (three dots) and choose **Custom repositories**.
2. Add `https://github.com/Citizen07/wialon-homeassistant` with type
   **Integration**.
3. Search for **Wialon**, open it and choose **Download**.
4. Restart Home Assistant.

### Manual

Copy the `custom_components/wialon` folder into the `custom_components`
folder of your Home Assistant configuration and restart Home Assistant.

## Configuration

Go to **Settings** > **Devices & services** > **Add integration** and choose
**Wialon**.

1. **Web address.** Pick one of the presets or enter the address you use to
   sign in to Wialon. The API host is detected from the login page. If
   detection fails, the integration assumes Wialon Local on the same host; you
   can also enter the API address yourself.
2. **Sign-in method.**
   - **Sign in with Wialon** opens the Wialon login page. After you sign in,
     the browser is redirected back to Home Assistant at
     `/api/wialon/oauth/<flow id>`, so Home Assistant must be reachable from
     your browser at its configured internal or external URL (see
     **Settings** > **System** > **Network**).
   - **Paste an access token** uses a 72-character token you create yourself,
     for example when Home Assistant is not reachable from the browser you
     sign in with. Make it read-only (`access_type=768`) with no expiry
     (`duration=0`) using Wialon's authorization page, `login.html` on your
     web address (see Gurtam's
     [authorization guide](https://blog.gurtam.com/storage/old_en/2015/07/New-Wialon-Authorization-Method_EN.pdf)).
     After you sign in, the token is the `access_token` parameter of the
     address the page redirects to.
3. **Units.** Select the units to track.

### Options

Open the integration and choose **Configure** to change the selected units and
the update interval (30 to 3600 seconds, default 60).

## Entities

Per unit:

- **Device tracker** with the GPS position.
- **Sensors:** speed, course, altitude, satellites and last message.
- **Parameter sensors and binary sensors.** Xirgo XG parameters reported as
  `sensor<ID>`, and some named keys such as J1939 and J1979, are converted with
  the Xirgo sensor table. Parameters with an ID below 8192 become binary
  sensors (doors, ignition and so on). A raw value of 0 on a sensor that has an
  offset means "no reading", and the entity is unknown.
- Unmapped, duplicate and user-defined parameters are created disabled. Enable
  them in the entity settings if you need them.

## Action: `wialon.import_history`

Backfills hourly long-term statistics from the messages Wialon has stored, for
the enabled numeric sensors of the selected units.

| Field | Description |
| --- | --- |
| `device_id` | Units to import. Leave empty for all selected units. |
| `days` | How many days back to import, 1 to 366. Default 365. |

```yaml
action: wialon.import_history
data:
  device_id:
    - 0123456789abcdef0123456789abcdef
  days: 90
```

The action is idempotent: it only writes hours before an entity's first
existing statistic. It runs in the background and posts a notification when
done.

## Diagnostics

Download diagnostics from the integration page. The file includes the latest
raw data of each unit, which is useful for finding out what a tracker reports.
The token, account name, unit names, IMEIs, phone numbers, device password and
positions are redacted. The raw tracker parameters are not, and some trackers
report coordinates or identifiers among them, so read the file before you
attach it to a public issue.

## Limitations

- Cloud polling: data is as fresh as the poll interval.
- Binary sensors and the device tracker are not backfilled by
  `wialon.import_history`, because Home Assistant has no supported way to
  backfill states.
- Parameters outside the Xirgo table stay raw and disabled.

## Development

Install the test requirements and run the tests:

```
pip install -r requirements_test.txt
pytest
```

`requirements_test.txt` needs Linux or macOS. On Windows, install
`requirements_dev.txt` instead; it runs the tests that do not need Home
Assistant, and the config flow tests are skipped.

Regenerate the Xirgo sensor table with:

```
python scripts/gen_xirgo_sensors.py
```

## Attribution

The Xirgo sensor table is generated from the public Xirgo documentation at
docs.xirgoglobal.com.

This project is not affiliated with or endorsed by Gurtam (Wialon) or Xirgo
Global. All trademarks belong to their respective owners.

## License

MIT. See [LICENSE](LICENSE).
