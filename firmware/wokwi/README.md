# CivicTwin-R — Wokwi Simulations

## Files

- `diagram.json` — Circuit diagram (ESP32 DevKit + HC-SR04 ultrasonic sensor + Alert LED)
- `wokwi.toml` — Local forwarding configuration

The canonical firmware is `firmware/esp32/src/main.ino`. The circuit in
`firmware/wokwi/diagram.json` is shared by all three simulations.

## Project configurations

Each Wokwi project uses the same hardware and firmware logic. The project-specific
configuration section in `main.ino` is canonical for Adyar by default:

| Study area | Zone ID | Device ID | MQTT client ID |
|---|---|---|---|
| Adyar | `zone_01` | `node_adyar_01` | `civictwin_adyar_01` |
| Cooum | `zone_02` | `node_cooum_01` | `civictwin_cooum_01` |
| Kosasthalaiyar | `zone_03` | `node_kosasthalaiyar_01` | `civictwin_kosasthalaiyar_01` |

For each separate Wokwi project:

1. Copy the existing `firmware/wokwi/diagram.json` into the project.
2. Copy the canonical `firmware/esp32/src/main.ino` into the project.
3. Change only `MQTT_CLIENT_ID`, `DEVICE_ID`, and `ZONE_ID` in the marked
   project configuration section.
4. Leave the sensor logic, pins, timing, filtering, thresholds, JSON schema,
   and `DATA_SOURCE` unchanged.

`ZONE_ID` is used to derive the telemetry, status, and alert topics:

```text
civictwin/v1/zones/{ZONE_ID}/telemetry
civictwin/v1/zones/{ZONE_ID}/status
civictwin/v1/zones/{ZONE_ID}/alerts
```

Therefore, each project's MQTT client ID and device ID must be unique. Its
payload `zone_id` and all topic zone segments must consistently identify the
same canonical zone. All telemetry must retain the `WOKWI_SIMULATED` source
value. These projects represent virtual laboratory prototypes, not direct
measurements from Chennai's rivers.

## Simulated rain indicator

The circuit includes a `wokwi-potentiometer` as a controllable, uncalibrated
stand-in for an analog rain sensor. Its signal connects directly to the ESP32:

```text
potentiometer SIG -> ESP32 VP / GPIO 36
```

| Potentiometer pin | ESP32 connection |
|---|---|
| `VCC` | `3V3` |
| `GND` | `GND` |
| `SIG` | `VP` / GPIO 36 (ADC1 input) |

To demonstrate different conditions, turn the potentiometer through its range:

- Low setting: dry demonstration
- Middle setting: light or moderate rain demonstration
- High setting: heavy rain demonstration

The firmware publishes:

- `rain_sensor_raw`: ADC count from `0` to `4095`
- `rain_indicator_pct`: normalized indicator from `0` to `100`

This is an uncalibrated simulated indicator only. It is not rainfall in
millimetres, millimetres per hour, or an observation from Chennai. Wokwi
telemetry retains `WOKWI_SIMULATED` and the
`rain_indicator_uncalibrated` quality flag.

## How to Run

1. Open [wokwi.com](https://wokwi.com)
2. Create three separate ESP32 projects, one for each study area.
3. For each project, copy the shared `diagram.json` and canonical sketch as
   described above.
4. Apply only that project's identity values from the table.
5. Press the green **Start Simulation** button.

Do not add Wokwi project links to this repository until the projects have been
created and saved in the owner's Wokwi account.

The Docker backend can be placed in Wokwi live mode without changing the
firmware or broker configuration. In that mode, the dashboard accepts only
`WOKWI_SIMULATED` telemetry and shows `WAITING FOR WOKWI DATA` until a project
publishes. Browser Wokwi still needs an explicitly approved connection method;
do not assume `localhost`, expose the anonymous broker publicly, or commit
credentials.

## Simulating Water Levels

- Click the HC-SR04 ultrasonic sensor during simulation.
- Use the slider to change the simulated distance.
- **Formulas:**
  - $\text{Water depth (cm)} = 30.0\text{ cm} - \text{measured distance (cm)}$ (clamped to $[0.0, 30.0]$ cm)
  - $\text{Water level (\%)} = 100 \times \frac{\text{Water depth (cm)}}{30.0\text{ cm}}$ (clamped to $[0.0\%, 100.0\%]$)
- **Threshold Testing:**
  - Warning Alert triggered at $\ge 70.0\%$ (depth $\ge 21.0\text{ cm}$, distance $\le 9.0\text{ cm}$). Onboard LED illuminates.
  - Critical Alert triggered at $\ge 85.0\%$ (depth $\ge 25.5\text{ cm}$, distance $\le 4.5\text{ cm}$).
  - Alerts clear with a $3.0\%$ hysteresis band (level must drop below $67.0\%$ to return to normal).
  - *Disclaimer: These are prototype test thresholds, not official flood warning levels for Chennai.*

## MQTT Connectivity

The firmware currently keeps the broker configuration unchanged:

```text
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
```

`localhost` in Wokwi is not the developer's Mac, and Docker service names such
as `mosquitto` are not reachable from Wokwi. Do not assume that a Mac LAN IP is
reachable from Wokwi. A secure, approved, externally reachable MQTT endpoint
with authentication and TLS must be selected before changing these settings.
Do not expose the current anonymous local Mosquitto broker publicly and do not
switch to a public broker without explicit approval.

When offline, the firmware still performs local median filtering, threshold
evaluation, and LED alerts. No broker or networking configuration is changed by
this README.
