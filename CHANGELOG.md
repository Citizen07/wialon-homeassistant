# Changelog

## 0.2.0

First public release.

- Config flow with Wialon Hosting and Wialon Local support; the API host is detected from the login page.
- Sign in through the Wialon login page or with a pasted read-only token; reauthentication when the token is revoked.
- Per unit: GPS device tracker, speed, course, altitude, satellites and last message sensors.
- Xirgo XG parameters converted with the Xirgo sensor table (sensors and binary sensors); unmapped parameters are created disabled.
- Options flow for the selected units and the poll interval.
- `wialon.import_history` action to backfill hourly long-term statistics.
- Diagnostics download with the token, account, unit names, IMEIs, phone numbers, device password and positions redacted.
