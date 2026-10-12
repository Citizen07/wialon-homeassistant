# Changelog

## 0.2.3

- The GPS tracker no longer writes a state, a recorder row and a `state_changed` event on every poll while the unit stands still. Home Assistant forces those writes for push-style trackers such as phone apps; a Wialon unit that moves changes its coordinates, which still writes. `last_changed` now tells when the tracker actually changed.

## 0.2.2

- Each poll also reads the messages received since the previous poll (one extra `messages/load_interval` call per unit, only when it has new messages; skipped on the first poll and after gaps over 6 hours). Numeric parameter sensors expose the extremes of that window as the `window_min`, `window_max` and `window_to` attributes, so short events between polls are no longer lost.
- Wialon's eco-driving parameters `wln_accel_max`, `wln_brk_max` and `wln_crn_max` become sensors (in g).
- `wialon.import_history` run before Home Assistant had compiled any statistics for a counter (distance, fuel used) left a step down the size of the whole import where its rows met the recorder's own: the recorder starts its sum at 0 because an import writes no short-term statistics. Imported sums now end at 0 in that case, so the two join. Installs already affected can fix the step with Developer Tools → Statistics → adjust sum at the first hour Home Assistant recorded itself.

## 0.2.1

- Parameter sensors and binary sensors read Wialon's accumulated parameter store, so a value no longer drops to unknown when the latest message omits it. A new `last_changed` attribute tells when the tracker last changed the value.
- Parameters that were reported before but are missing from the latest message now get their entities too; most are created disabled.
- The store's `posinfo` (the last position) does not become a sensor and is redacted in diagnostics. Structured values like it are skipped, with a debug log line once per key.
- Tags with a suffix (such as `v0.2.2b0`) are published as pre-releases, which HACS offers only with "Show beta versions".

## 0.2.0

First public release.

- Config flow with Wialon Hosting and Wialon Local support; the API host is detected from the login page.
- Sign in through the Wialon login page or with a pasted read-only token; reauthentication when the token is revoked.
- Per unit: GPS device tracker, speed, course, altitude, satellites and last message sensors.
- Xirgo XG parameters converted with the Xirgo sensor table (sensors and binary sensors); unmapped parameters are created disabled.
- Options flow for the selected units and the poll interval.
- `wialon.import_history` action to backfill hourly long-term statistics.
- Diagnostics download with the token, account, unit names, IMEIs, phone numbers, device password and positions redacted.
