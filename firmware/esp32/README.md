# CivicTwin-R — ESP32 Firmware

## Overview

Arduino C++ firmware for the ESP32 with an HC-SR04 ultrasonic distance sensor.
Reads distance, computes water depth and percentage level, applies local threshold logic with hysteresis, and publishes JSON telemetry over MQTT.

> **Important Study Area Disclaimer:**  
> In simulation mode (Wokwi), this virtual node (`node_01`) is explicitly mapped to **Adyar Basin (`zone_01` / `zone_adyar`)**. It represents a virtual laboratory prototype and does **not** imply that physical hardware has been deployed in Chennai rivers, nor does a single simulated node measure all three metropolitan basins.

## Circuit Wiring & Pinout

| HC-SR04 Pin | ESP32 Pin | Function | Notes |
|-------------|-----------|----------|-------|
| VCC | VIN (5V) | Power | HC-SR04 requires 5V supply |
| GND | GND | Ground | Common ground reference |
| TRIG | GPIO 5 | Trigger pulse | 3.3V GPIO pulse initiates 40 kHz ultrasound burst |
| ECHO | GPIO 18 | Echo pulse input | **⚠ REQUIRES VOLTAGE DIVIDER ON PHYSICAL HARDWARE** |
| Status LED | GPIO 2 | Threshold Alert | High on Warning/Critical, Low on Normal (220Ω resistor) |

### Voltage Protection (Physical Hardware Only)

The HC-SR04 `ECHO` pin outputs 5V TTL logic. ESP32 GPIOs are 3.3V tolerant.
For physical hardware assembly, a passive resistor voltage divider is mandatory:

```
HC-SR04 ECHO ──┬── 2.2kΩ ──── ESP32 GPIO 18
               │
              3.3kΩ
               │
              GND
```

Calculation: $V_{\text{out}} = 5\text{V} \times \frac{3.3\text{k}\Omega}{2.2\text{k}\Omega + 3.3\text{k}\Omega} \approx 3.0\text{V}$ (safe for ESP32).  
*Note: In Wokwi browser simulation, no physical voltage divider is required.*

## Water Level Calculation & Geometry

The sensor is mounted above a channel or container with a known reference height:

$$\text{Water Depth (cm)} = \text{Container Reference Height (cm)} - \text{Measured Distance (cm)}$$
$$\text{Water Level (\%)} = 100 \times \frac{\text{Water Depth (cm)}}{\text{Container Reference Height (cm)}}$$

- **Reference Height:** Configurable via `CONTAINER_HEIGHT_CM` (default: **30.0 cm**).
- **Assumptions:** Fixed downward-facing sensor mount, stationary baseline, open channel/container.
- **Boundaries:** Depth clamped to $[0.0, 30.0]\text{ cm}$; Percentage clamped to $[0.0\%, 100.0\%]$.
- **Invalid Readings:** Echo timeouts or distances outside $[2.0, 400.0]\text{ cm}$ are marked `sensor_status: "invalid"`. Null values (`nullptr`) are emitted rather than fabricating a zero-depth reading.

## Warning & Critical Thresholds

- **Warning Threshold:** `70.0%`
- **Critical Threshold:** `85.0%`
- **Hysteresis Band:** `3.0%` (prevents alert oscillation when levels hover near threshold)
- **Local Indicator:** Onboard LED on GPIO 2 illuminates when Warning or Critical state is reached.

> **Prototype Threshold Disclaimer:**  
> These threshold values (70% warn, 85% crit) are prototype settings for testing cyber-physical digital twin pipelines. They are **not** officially validated, legal, or calibrated flood-warning thresholds for the Chennai river basins.

## Configuration Constants in `main.ino`

| Constant | Default | Description |
|---|---|---|
| `DEVICE_ID` | `"node_adyar_01"` | Edge sensor node identifier |
| `ZONE_ID` | `"zone_01"` | Associated study zone (`zone_adyar`) |
| `DATA_SOURCE` | `"WOKWI_SIMULATED"` | Telemetry data provenance tag |
| `WIFI_SSID` | `"Wokwi-GUEST"` | Wi-Fi network SSID |
| `MQTT_BROKER` | `"wfde4d21.ala.asia-southeast1.emqxsl.com"` | EMQX Cloud hostname |
| `MQTT_PORT` | `8883` | MQTT over TLS port |
| `CONTAINER_HEIGHT_CM`| `30.0` | Channel reference container height (cm) |
| `READ_INTERVAL_MS` | `5000` | Sensor sampling interval (5 seconds) |
| `WARN_THRESHOLD_PCT` | `70.0` | Warning alert trigger (%) |
| `CRIT_THRESHOLD_PCT` | `85.0` | Critical alert trigger (%) |
| `HYSTERESIS_PCT` | `3.0` | Alert hysteresis dampener (%) |
| `FILTER_READINGS` | `5` | Median filtering window size |

## Running in Wokwi (Recommended Simulation)

1. Open [wokwi.com](https://wokwi.com) → ESP32 project.
2. Replace `diagram.json` with `firmware/wokwi/diagram.json`.
3. Paste `firmware/esp32/src/main.ino` into the code editor.
4. Click **Start Simulation**.
5. In the Serial Monitor, confirm `[MQTT] Connected`. Wokwi's public gateway
   provides internet access for the simulation, but does not provide access to
   your local Docker broker. A valid local EMQX device password is required.
6. Drag the HC-SR04 distance slider:
   - High distance (e.g. 28 cm) $\rightarrow$ low water level ($2\text{ cm} \approx 6.7\%$).
   - Low distance (e.g. 5 cm) $\rightarrow$ high water level ($25\text{ cm} \approx 83.3\%$, Warning Alert + LED ON).
   - Very low distance (e.g. 3 cm) $\rightarrow$ critical water level ($27\text{ cm} \approx 90.0\%$, Critical Alert + LED ON).
