# CivicTwin-R — Installation & Local Demo Guide

## Two operating modes

| Mode | When to use | MQTT | Storage | Dashboard |
|------|-------------|------|---------|-----------|
| **A — Docker** | Full demonstration with Node-RED dashboard | Mosquitto container | SQLite + InfluxDB | Node-RED on port 1880 |
| **B — Standalone Mac** | Quick dev/test without Docker | No broker (simulator only) | SQLite | FastAPI docs only |

---

## Mode A — Docker Desktop (recommended for the demo)

### Prerequisites

| Tool | Version | Check |
|------|---------|-------|
| Docker Desktop | 4.x+ | `docker --version` |
| Docker Compose | V2 (bundled) | `docker compose version` |
| Git | 2.x | `git --version` |

> **Important:** Start Docker Desktop and wait for the whale icon in the menu bar to stop animating before running any commands.

---

### Step 1 — Open Terminal in the project root

```bash
cd "/Users/neilnickson/Documents/iot/iot project/IoT-Project"
```

### Step 2 — Create your local `.env` file

```bash
cp .env.example .env
```

The defaults in `.env.example` work out of the box for local Docker development.
The only value you may want to change is `INFLUXDB_TOKEN` — use any non-empty string;
the same value must appear in both `INFLUXDB_TOKEN` and `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN`
(the Compose file passes `$INFLUXDB_TOKEN` to both, so they stay in sync automatically).

For the EMQX Cloud backend connection, edit only the local, Git-ignored `.env`
and set the MQTT values from `.env.example`:

```text
MQTT_BROKER_HOST=wfde4d21.ala.asia-southeast1.emqxsl.com
MQTT_BROKER_PORT=8883
MQTT_USERNAME=civictwin_backend
MQTT_PASSWORD=<your EMQX password>
MQTT_CLIENT_ID=civictwin_backend_01
MQTT_TLS_ENABLED=true
MQTT_TLS_CA_FILE=
```

Leaving `MQTT_TLS_CA_FILE` empty uses Python's default trusted CA certificates
with hostname verification enabled. Do not disable certificate verification,
put the password in tracked files, or change the local Mosquitto service.

### Step 3 — Start all services

```bash
docker compose up -d --build
```

`--build` rebuilds the backend image when source code has changed.
Omit it on subsequent starts if you have not changed Python code:

```bash
docker compose up -d
```

### Step 4 — Verify service status

```bash
docker compose ps
```

Expected output (all four services `running` or `healthy`):

```
NAME                    IMAGE                        STATUS
civictwin-mosquitto     eclipse-mosquitto:2.0        running (healthy)
civictwin-influxdb      influxdb:2.7                 running (healthy)
civictwin-backend       civictwin-r-backend          running
civictwin-nodered       nodered/node-red:4.0         running
```

> InfluxDB and Mosquitto have health checks. The backend and Node-RED
> wait for them before starting, so allow ~30 seconds on the first run.

### Step 5 — Verify individual services

**Backend API health:**
```bash
curl http://localhost:8000/health
```
Expected: `{"status":"healthy",...}`

**InfluxDB ping:**
```bash
curl http://localhost:8086/ping
```
Expected: HTTP 204 (no output, exit 0)

**Mosquitto — publish a test message:**
```bash
# Requires mosquitto-clients installed on your Mac (brew install mosquitto)
mosquitto_pub -h localhost -p 1884 -t "civictwin/v1/test" -m "hello"
```

**Node-RED editor:**
Open [http://localhost:1880](http://localhost:1880) in your browser.

**Node-RED dashboard:**
Open [http://localhost:1880/ui](http://localhost:1880/ui) in your browser.

**FastAPI interactive docs:**
Open [http://localhost:8000/docs](http://localhost:8000/docs) in your browser.

**InfluxDB UI:**
Open [http://localhost:8086](http://localhost:8086) in your browser.
Login: username `admin`, password `civictwin_local`.

### Step 6 — Verify InfluxDB is receiving telemetry

After the backend has been running for 30+ seconds with telemetry active:

1. Open the InfluxDB UI at [http://localhost:8086](http://localhost:8086)
2. Go to **Data Explorer**
3. Select bucket `telemetry`, measurement `telemetry`
4. If points appear, InfluxDB write is working

Alternatively via the CLI (requires `influx` CLI or `curl`):
```bash
curl -H "Authorization: Token civictwin-dev-token" \
     "http://localhost:8086/api/v2/query?org=civictwin" \
     --data-urlencode 'q=from(bucket:"telemetry") |> range(start:-5m) |> limit(n:3)' \
     -H "Content-Type: application/vnd.flux"
```

> If no points appear yet, InfluxDB write is still initialising. Check
> `docker compose logs backend` for `InfluxDB storage initialized` or
> `InfluxDB connection failed` messages.

### Step 7 — View logs

```bash
docker compose logs -f              # all services
docker compose logs -f backend      # backend only
docker compose logs -f mosquitto    # broker only
```

### Step 8 — Install Node-RED dashboard plugin (first run only)

The flows require the `node-red-dashboard` package.
On first start, open the Node-RED editor at [http://localhost:1880](http://localhost:1880) and check:

1. Go to **Menu (≡) → Manage Palette → Install**
2. Search for `node-red-dashboard`
3. Install `node-red-dashboard` (for Node-RED 3.x) **or** `@flowfuse/node-red-dashboard` (for Node-RED 4.x)
4. Click **Deploy**
5. Open [http://localhost:1880/ui](http://localhost:1880/ui)

### Step 9 — Stop services safely (data is preserved)

```bash
docker compose down
```

This stops and removes containers but **keeps all Docker volumes** intact
(`mosquitto_data`, `influxdb_data`, `nodered_data`).
Restart at any time with `docker compose up -d`.

> **Do NOT run** `docker compose down -v` unless you intentionally want
> to delete all stored MQTT messages, InfluxDB time-series data, and
> Node-RED settings.

---

## Connecting Wokwi to the MQTT Broker

### Why `localhost` does not work in browser Wokwi

Wokwi runs in your browser as a sandboxed simulation. The ESP32 firmware
uses `MQTT_BROKER = "localhost"`, but inside the Wokwi sandbox `localhost`
resolves to Wokwi's own servers — **not** your Mac or any Docker container.

The Docker backend uses `mosquitto:1883` internally. Docker maps the broker to
Mac port `1884` for local host clients and port `9001` for WebSockets. The
anonymous local listener must not be exposed publicly.

### Option A — Use an approved local Wokwi forwarding method

The local Wokwi desktop/VS Code workflow may forward the simulated device's
MQTT connection to the Mac host. If you use that workflow, target the
host-side broker port `1884`; do not change the Docker service address used by
the backend. Confirm the forwarding behavior in your chosen Wokwi client
before changing firmware.

### Option B — Use your Mac's LAN IP address (not guaranteed)

1. Find your Mac's local IP:
   ```bash
   ipconfig getifaddr en0
   # or for Wi-Fi: ipconfig getifaddr en1
   ```
   Example result: `192.168.1.105`

2. In `firmware/esp32/src/main.ino`, change line 26:
   ```cpp
   // Before
   const char* MQTT_BROKER = "localhost";

   // After (only after explicit approval)
   const char* MQTT_BROKER = "192.168.1.105";   // your actual LAN IP
   ```

3. Ensure host port 1884 is accessible from your Mac's local network
   (Docker maps host port 1884 to the broker's container port 1883).

4. Paste the updated sketch into Wokwi and run the simulation.

> **Note:** This only works if your Mac and Wokwi's simulation network
> can reach the same LAN IP. This is normally true on a home/lab network
> but may fail on corporate networks with client isolation enabled.

### Option C — Use a separately approved secure external MQTT endpoint

This requires an authenticated, TLS-protected endpoint and explicit approval.
Do not use an anonymous public broker, enable port forwarding, or place
credentials in firmware or committed files.

### MQTT topic contract

The firmware publishes to: `civictwin/v1/zones/zone_01/telemetry`

The backend subscribes to: `civictwin/v1/zones/+/telemetry`

Both use the same prefix. Do not change the topic structure.

---

## Mode B — Standalone Mac (no Docker)

```bash
# 1. Install Python dependencies
python3 -m pip install -e ".[dev]"

# 2. Create .env (set INFLUXDB_ENABLED=false, STORAGE_BACKEND=sqlite)
cp .env.example .env

# 3. Run the test suite
python3 -m pytest tests/ -v

# 4. Start the backend (uses Python simulator, no MQTT broker needed)
python3 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000

# 5. Open API docs
open http://127.0.0.1:8000/docs
```

In this mode:
- The Python simulator (`backend/simulator.py`) generates synthetic telemetry internally.
- No MQTT broker is needed.
- All data is stored in `data/civictwin.db` (SQLite).
- Node-RED is **not** available (it runs in Docker only).

---

## Quick reference — Local URLs

| Service | URL | Notes |
|---------|-----|-------|
| Backend API | http://localhost:8000/docs | Interactive Swagger UI |
| Backend health | http://localhost:8000/health | JSON health check |
| Node-RED editor | http://localhost:1880 | Flow editor |
| Node-RED dashboard | http://localhost:1880/ui | Live dashboard |
| InfluxDB UI | http://localhost:8086 | Time-series explorer |
| Mosquitto TCP | localhost:1884 | MQTT clients on the Mac host |
| Mosquitto WebSocket | localhost:9001 | Browser MQTT clients |

---

## Troubleshooting

See [`docs/troubleshooting.md`](troubleshooting.md) for common issues.

**Common first-run issues:**

| Problem | Cause | Fix |
|---------|-------|-----|
| Backend exits immediately | InfluxDB not ready yet | `docker compose restart backend` after 30s |
| Node-RED shows no data | Dashboard plugin not installed | Install `node-red-dashboard` via palette manager |
| Wokwi cannot connect to broker | `localhost` unreachable from Wokwi | Use LAN IP or public broker (see above) |
| InfluxDB `409 Conflict` on startup | Volume already initialised | Normal — ignored safely on subsequent runs |
| `docker compose` not found | Docker Desktop not running | Start Docker Desktop and wait for it to be ready |
