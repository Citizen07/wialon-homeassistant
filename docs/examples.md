# Example: a car dashboard built on Wialon

The [`examples/`](../examples) folder holds a complete Home Assistant setup
built on this integration's entities: tracker health, the 12 V battery,
trips and engine, fuel, security and maintenance, with alerts and a
dashboard view. It comes from a setup that has run on two cars (a hybrid
and a petrol car with Xirgo trackers), generalised so you can adapt it to
yours.

Most of the logic exists because the raw data misleads in specific ways:
values that are held after the tracker stops sending them, CAN bits for
doors a car does not have, fuel gauges that read 0 at start-up or stay at
100 % for days. The comments in each file explain the reason for each rule,
and [Why the logic looks like this](#why-the-logic-looks-like-this) below
lists the main ones.

## Files

| File | What it gives you |
| --- | --- |
| [`custom_templates/wialon_vehicles.jinja`](../examples/custom_templates/wialon_vehicles.jinja) | Per-car settings (`vehicle_cars`) and the shared macros: trip accumulator, fuel and refuel log. |
| [`packages/vehicle_notify.yaml`](../examples/packages/vehicle_notify.yaml) | `script.vehicle_notify`, the one place alerts go out: a persistent notification plus a push. |
| [`packages/vehicle_health.yaml`](../examples/packages/vehicle_health.yaml) | Car **in use**, which the other packages build on. Tracker silent, Wialon unreachable, weak GNSS fix, tracker power and backup cell, wheel-to-GNSS speed ratio. |
| [`packages/vehicle_battery.yaml`](../examples/packages/vehicle_battery.yaml) | 12 V resting voltage (24 h mean), drain per day while parked, charging voltage, charging hours per week. Alerts on a low battery or a charging fault. |
| [`packages/vehicle_engine.yaml`](../examples/packages/vehicle_engine.yaml) | The current trip and a log of the last 20: duration, km, fuel, idle minutes, coolant warm-up. EV share for hybrids, cold-trip share, stuck-open thermostat check. |
| [`packages/vehicle_fuel.yaml`](../examples/packages/vehicle_fuel.yaml) | Litres in the tank, refuel log with pump litres, consumption (last 10 trips, and full to full), range. Alert on fuel lost while parked. |
| [`packages/vehicle_security.yaml`](../examples/packages/vehicle_security.yaml) | Left open, left unlocked, lights left on, moved while parked (towed or stolen), door opened while armed, factory alarm. |
| [`packages/vehicle_maintenance.yaml`](../examples/packages/vehicle_maintenance.yaml) | Service by km and by months, inspection and insurance reminders, winter-tyre reminder, odometer-to-GNSS ratio. |
| [`dashboards/vehicles_view.yaml`](../examples/dashboards/vehicles_view.yaml) | A dashboard view with the track map and a card per package. |

Install `vehicle_notify.yaml` and `vehicle_health.yaml` first; the others
depend on them. Beyond that, take only the packages you want. Each package
also lists at its top the entities it needs.

## Sensors needed

What your tracker must report for each feature. `<unit>` is your unit's
entity prefix. The Xirgo ID is the number in the raw parameter key
(`sensor12288` is ID 12288), which you can look for in the integration's
diagnostics download. Some trackers send the same value under a named key
instead (`can_taho`, `total_fuel`, `can_mileage`, `engine_temp`); the
integration maps those to the same entities.

Rows marked *disabled* are created disabled; enable them in the entity
settings.

| Entity | Source | Used for | Without it |
| --- | --- | --- | --- |
| `device_tracker.<unit>` | GPS position | Parked position, moved while parked, map | Required for security. |
| `sensor.<unit>_last_message` | Every unit | Tracker silent, Wialon unreachable | Required for health. |
| `sensor.<unit>_speed` | Every unit (GNSS) | In use (no ignition input), idle minutes, moved while parked, wheel speed ratio | Required. |
| `sensor.<unit>_satellites` | Every unit | GNSS weak, moved-while-parked distance check | Required. |
| `sensor.<unit>_hdop` | Wialon parameter `hdop`, *disabled* | GNSS weak, moved-while-parked distance check | GNSS weak uses satellites only; moved while parked reacts to speed only, not to the car being 300 m away. |
| `sensor.<unit>_track`, `date.<unit>_track_date` | Every unit (0.3.0+) | Track map in the dashboard | No map line. |
| `binary_sensor.<unit>_ignition` | Xirgo 109, SENSOR_IGNITION | In use, battery resting readings, moved while parked | Set `ignition: false`; engine speed and movement stand in. |
| `binary_sensor.<unit>_ready_to_drive` | Xirgo 142, SENSOR_READY_TO_DRIVE | Hybrids: in use, charging, EV share | Only needed with `hybrid: true`. |
| `sensor.<unit>_engine_speed` | Xirgo 12300, SENSOR_ENGINE_SPEED (CAN) | Charging (non-hybrid), EV share, in use and moved while parked without an ignition input | Charging stays off on a non-hybrid, so no charging voltage, charging hours or charging fault. |
| `sensor.<unit>_vcc` | Xirgo 12288, SENSOR_VCC | The whole battery package; external power lost | Required for battery. |
| `sensor.<unit>_vbat` | Xirgo 12292, SENSOR_VBAT | Backup cell missing and low | No backup cell checks. |
| `sensor.<unit>_wheel_speed` | Xirgo 12317, SENSOR_WHEEL_SPEED (CAN) | Wheel to GNSS speed ratio | Delete that block. |
| `sensor.<unit>_distance` | Xirgo 45058, SENSOR_DISTANCE (metres) | Trip km, odometer to GNSS ratio, "not moved yet" for fuel loss | No trip km or consumption, no odometer ratio, no fuel loss alerts. |
| `sensor.<unit>_total_distance` | Xirgo 16387, SENSOR_TOTAL_DISTANCE (odometer) | Service km left, odometer ratio, km per day and month graphs, full-to-full consumption | No km-based service reminder, graphs or full-to-full figure. |
| `sensor.<unit>_fuel_level_1` | Xirgo 8199, SENSOR_FUEL_LEVEL_1 | The whole fuel package; trip fuel when there is no counter | Required for fuel. |
| `sensor.<unit>_total_fuel_used` | Xirgo 16385, SENSOR_TOTAL_FUEL_USED | Trip fuel (`fuel_counter: true`) | Set `fuel_counter: false`; trip fuel comes from the gauge, or not at all with `sloshes: true`. |
| `sensor.<unit>_engine_temperature` | Xirgo 8200, SENSOR_ENGINE_TEMPERATURE | Warm-up, cold-trip share, thermostat check | Set `coolant: false`; those three stay empty. |
| `binary_sensor.<unit>_doors_f_l`, `_doors_f_r`, `_doors_r_l`, `_doors_r_r`, `_trunk`, `_bonnet` | Xirgo 98–103, SENSOR_DOORS_*, SENSOR_TRUNK, SENSOR_BONNET | Left open, door opened while armed | List only the working ones in `doors`. |
| `binary_sensor.<unit>_locked` | Xirgo 97, SENSOR_LOCKED | Left unlocked | No unlocked alert. |
| `binary_sensor.<unit>_armed` | Xirgo 96, SENSOR_ARMED | Door opened while armed | No armed-door alert. |
| `binary_sensor.<unit>_headlight_indicator`, `_parking_light_indicator` | Xirgo 136 and 135, *disabled* | Lights left on | No lights alert. |
| `binary_sensor.<unit>_factory_alarm` | Xirgo 108, SENSOR_FACTORY_ALARM, *disabled* | Factory alarm alert | No factory alarm alert. |
| `sensor.outdoor_temperature` | Any outdoor sensor, not Wialon | Outdoor temperature per trip, early winter-tyre reminder | Trips log no temperature; the tyre reminder goes by date only. |

A missing entity does not break a package: the checks built on it read
"off" or stay empty. Check a sensor's history before you rely on it,
because an entity can exist and still never report anything useful.

## Prerequisites

- This integration, 0.3.1 or newer: 0.2.1 added the parameter store and
  the `last_changed` attribute the packages time everything by, 0.3.0 the
  track sensor and track date for the map, and 0.3.1 stops counters from
  dropping to 0 when the CAN bus wakes.
- A tracker that reports the parameters you want. The packages use Xirgo
  sensor names (see the [Entities](../README.md#entities) section of the
  README). Other trackers work where their parameters map to the same
  entities, or after you change the entity IDs.
- [ha-map-card](https://github.com/nathan-gs/ha-map-card) from HACS
  (Frontend), only for the track map in the dashboard. Everything else uses
  built-in cards.
- Packages enabled in `configuration.yaml`:

  ```yaml
  homeassistant:
    packages: !include_dir_named packages
  ```

- The examples use the `triggers:` / `conditions:` / `actions:` syntax and
  were derived from a setup running on Home Assistant 2026.10.

## Install

1. Copy `examples/custom_templates/wialon_vehicles.jinja` to
   `<config>/custom_templates/` and the packages you want to
   `<config>/packages/`.
2. Adapt the entity IDs and settings (next section).
3. Enable the entities the packages use that the integration creates
   disabled: Wialon's raw `hdop` parameter (`sensor.<unit>_hdop`), and for
   `vehicle_security.yaml` the headlight and parking light indicators and
   the factory alarm, if your tracker reports them.
4. Check the configuration (**Developer tools** → **YAML** → **Check
   configuration**) and restart Home Assistant. Later edits need only the
   reloads: run `homeassistant.reload_custom_templates` **before** reloading
   template entities, or they render with the previous macros.
5. Set the maintenance date inputs right away (see
   [Maintenance inputs](#maintenance-inputs)).
6. Paste `examples/dashboards/vehicles_view.yaml` as a new view in a
   dashboard's raw configuration editor.

## Adapt it to your car

### Entity IDs

Two placeholders run through every file:

| Placeholder | Meaning | Example |
| --- | --- | --- |
| `my_unit` | The prefix the integration gave your unit's entities. Find it on the unit's device page: `sensor.family_van_vcc` means `family_van`. | `family_van` |
| `my_car` / `My car` | A short name for the entities these packages create. Entity IDs are made from the `name:`, so replace both spellings together. | `van` / `Van` |

Search and replace `my_unit`, `my_car` and `My car` in all the copied files,
including `wialon_vehicles.jinja` and the dashboard. Keep the car name
distinct from the unit prefix so the packages' entities cannot collide with
the integration's.

### Car settings

Open `wialon_vehicles.jinja` and edit the entry in `vehicle_cars`. Each key
is documented there; the ones that matter most:

- `hybrid`: a READY signal (hybrid, EV). Changes what "in use" and
  "charging" mean and adds the EV share.
- `ignition`: whether the tracker has an ignition input. Without it, engine
  speed or movement stands in.
- `coolant`: only if the engine temperature sensor really reports. Many
  trackers create it and send nothing usable for some cars.
- `fuel_unit`, `tank_l`, `saturates_at`, `sloshes`, `fuel_counter`: how
  your fuel gauge behaves. Look at a week of `fuel_level_1` history before
  you set them.
- `doors`: only the openings your car has and that report correctly.
- `outdoor`: an outdoor temperature sensor; `vehicle_maintenance.yaml` also
  uses `sensor.outdoor_temperature` for its 24-hour mean.

### Notifications

Every alert goes through `script.vehicle_notify` in `vehicle_notify.yaml`.
Replace `notify.notify` there with your own notify action, for example
`notify.mobile_app_<your_phone>`. The packages dismiss the persistent
notification when the condition clears.

### Maintenance inputs

A new date input starts at today's date, which reads as "due today". Right
after the first start, set each `input_datetime.my_car_*` to its real date,
or to 1970-01-01 for "not set". Number inputs start at 0, which already
means "not set". The service, inspection and insurance sensors stay
unavailable until their inputs are filled in.

The winter-tyre dates in `vehicle_maintenance.yaml` are an example; set them
to your local rule.

### More than one car

Add the car to `vehicle_cars` in `wialon_vehicles.jinja`, then either:

- add its entities next to the first car's in the same files (the alert
  automations take more entities in their trigger lists), or
- copy the packages under new file names, replace the placeholders with the
  second car's names, and make every automation `id` unique.

Some entities are shared and must exist once: `script.vehicle_notify`,
`binary_sensor.wialon_unreachable` (list every car's `last_message` in it),
`sensor.wialon_outages_7d` and `sensor.outdoor_temperature_24h_mean`.

## Why the logic looks like this

- **Values are held.** The integration keeps a parameter's last value
  through messages that omit it. `sensor.<unit>_last_message` is the only
  freshness signal; a parameter's `last_changed` attribute is when the
  tracker last changed it, by the tracker's clock. The battery, coolant and
  READY logic all time readings by `last_changed`, never by Home Assistant's
  own state timestamps, which move on restarts and reloads.
- **A stale READY.** A tracker sometimes sends no READY-off at the end of a
  trip. The held "on" then kept a hybrid "in use" indefinitely and silenced
  every parked alert. `binary_sensor.my_car_ready` ignores READY when the
  ignition is off and changed more recently than READY did.
- **Doors the car does not have.** Trackers report CAN bits for every door.
  On a car without rear doors, the "rear right door" bit started flipping
  while driving and often ended a trip "on", so "left open" fired after most
  trips. Only the openings in `doors` count.
- **One poll, many writes.** A poll writes the unit's entities one at a
  time, and templates re-render between them. At trip start the ignition
  lands before `last_message`; at trip end it goes off before the parked
  position is re-taken. Those instants looked like a silent tracker and a
  moved car. The `delay_on` on those sensors absorbs them.
- **Fuel gauges.** A reading of 0 at start-up is a glitch. Some gauges stay
  at 100 % for days after a fill, so litres near the top are a lower bound.
  Some slosh by tens of litres while driving, so only parked readings count
  there. The total-fuel-used counter, where present, gives per-trip fuel.
- **Held coolant.** At the start of a trip the coolant sensor still shows
  the end of the previous trip, until the tracker sends a new value. A trip
  counts coolant only once it was reported during that trip.
- **Baselines.** The odometer-to-GNSS ratio once took its baseline while the
  distance sensor was still unknown after a reload, and divided by the
  tracker's lifetime distance. It now only baselines on a real value.
- **Wialon outages.** When the integration cannot poll, every entity goes
  unavailable. The silence check is then unavailable too, so one outage is
  reported once, as Wialon unreachable, not as every tracker going silent.

## Before you rely on it

The packages read held values and a minute-by-minute poll, so durations and
shares are estimates at that resolution. Check each sensor's history before
putting it into an alert: a sensor that exists is not necessarily one that
reports.
