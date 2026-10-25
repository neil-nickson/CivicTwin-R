# CivicTwin-R — Node-RED Dashboard

## Overview

The Node-RED dashboard provides a visual monitoring interface with:
- **System Overview** — health status, MQTT connection, record counts, alerts
- **Water Level Monitoring** — per-zone gauges with warning/critical thresholds
- **Digital Twin Controls** — scenario selector, start/stop/step buttons
- **Resilience Laboratory** — sensor dropout and network outage triggers, alert feed

## Setup

### Install Node-RED

```bash
npm install -g node-red
```

### Install Dashboard Nodes

For Node-RED < 4.0:
```bash
cd ~/.node-red
npm install node-red-dashboard
```

For Node-RED 4.0+, use the built-in `@flowfuse/node-red-dashboard`.

### Import Flows

1. Start Node-RED: `node-red`
2. Open http://127.0.0.1:1880
3. Menu → Import → Clipboard
4. Paste contents of `flows.json`
5. Click Import → Deploy

### Access Dashboard

- Node-RED editor: http://127.0.0.1:1880
- Dashboard UI: http://127.0.0.1:1880/ui

## Requirements

The dashboard polls the FastAPI backend API. In Docker Wokwi live mode, the
backend accepts only `WOKWI_SIMULATED` telemetry as the live zone reading.
Zones without a device packet show `WAITING FOR WOKWI DATA`; stopped devices
show a stale/offline status with the last telemetry timestamp. Python simulator
and historical replay records remain available for standalone and replay
workflows but are not presented as live Wokwi readings.

Start the backend first:

```bash
python3 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

## How It Works

The dashboard uses HTTP request nodes to poll the backend API:
- `/health` every 10 seconds → system status widgets
- `/api/zones` every 5 seconds → water level gauges
- `/api/scenarios/status` every 5 seconds → scenario progress
- `/api/alerts` every 10 seconds → alert feed

Scenario controls send POST requests to start, stop, or step scenarios.
