"""
CivicTwin-R — Shared Configuration Module

Loads settings from environment variables (.env file) with safe defaults.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE)
else:
    load_dotenv(_PROJECT_ROOT / ".env.example")

# MQTT
MQTT_BROKER_HOST: str = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT: int = int(os.getenv("MQTT_BROKER_PORT", "1883"))
MQTT_USERNAME: str = os.getenv("MQTT_USERNAME", "")
MQTT_PASSWORD: str = os.getenv("MQTT_PASSWORD", "")
MQTT_CLIENT_ID: str = os.getenv("MQTT_CLIENT_ID", "civictwin_backend")
MQTT_TLS_ENABLED: bool = os.getenv("MQTT_TLS_ENABLED", "false").lower() == "true"
MQTT_TLS_CA_FILE: str = os.getenv("MQTT_TLS_CA_FILE", "")
MQTT_TOPIC_PREFIX: str = os.getenv("MQTT_TOPIC_PREFIX", "civictwin/v1")
MQTT_QOS: int = 1

# Live telemetry mode
# Empty LIVE_TELEMETRY_SOURCE preserves standalone behavior. Docker Wokwi mode
# sets this to WOKWI_SIMULATED so other generated streams cannot appear live.
PYTHON_SIMULATOR_ENABLED: bool = os.getenv(
    "PYTHON_SIMULATOR_ENABLED", "true"
).lower() == "true"
LIVE_TELEMETRY_SOURCE: str = os.getenv("LIVE_TELEMETRY_SOURCE", "").strip().upper()

# InfluxDB
INFLUXDB_URL: str = os.getenv("INFLUXDB_URL", "http://localhost:8086")
INFLUXDB_TOKEN: str = os.getenv("INFLUXDB_TOKEN", "")
INFLUXDB_ORG: str = os.getenv("INFLUXDB_ORG", "civictwin")
INFLUXDB_BUCKET: str = os.getenv("INFLUXDB_BUCKET", "telemetry")
INFLUXDB_ENABLED: bool = os.getenv("INFLUXDB_ENABLED", "false").lower() == "true"

# Storage
STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "sqlite")
SQLITE_DB_PATH: str = os.getenv("SQLITE_DB_PATH", str(_PROJECT_ROOT / "data" / "civictwin.db"))

# API
API_HOST: str = os.getenv("API_HOST", "127.0.0.1")
API_PORT: int = int(os.getenv("API_PORT", "8000"))
API_DEBUG: bool = os.getenv("API_DEBUG", "true").lower() == "true"
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# Digital Twin
TWIN_TIME_STEP_SEC: float = float(os.getenv("TWIN_TIME_STEP_SEC", "5"))
TWIN_DEFAULT_SCENARIO: str = os.getenv("TWIN_DEFAULT_SCENARIO", "normal")
RANDOM_SEED: int = int(os.getenv("RANDOM_SEED", "42"))

# Node-RED
NODERED_PORT: int = int(os.getenv("NODERED_PORT", "1880"))

# Sensor Defaults
SENSOR_MAX_DISTANCE_CM: float = 400.0
SENSOR_MIN_DISTANCE_CM: float = 2.0
CONTAINER_HEIGHT_CM: float = 30.0
WATER_LEVEL_WARN_PCT: float = 70.0
WATER_LEVEL_CRIT_PCT: float = 85.0
WATER_LEVEL_HYSTERESIS_PCT: float = float(os.getenv("WATER_LEVEL_HYSTERESIS_PCT", "3.0"))
STALE_READING_TIMEOUT_SEC: float = 30.0

# Paths
DATA_DIR = _PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
GENERATED_DIR = DATA_DIR / "generated"
RESULTS_DIR = _PROJECT_ROOT / "results"
PLOTS_DIR = RESULTS_DIR / "plots"
TABLES_DIR = RESULTS_DIR / "tables"
LOGS_DIR = RESULTS_DIR / "logs"

for d in [RAW_DIR, PROCESSED_DIR, GENERATED_DIR, PLOTS_DIR, TABLES_DIR, LOGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def summary() -> dict:
    """Return a safe summary of current configuration (no secrets)."""
    return {
        "mqtt_broker": f"{MQTT_BROKER_HOST}:{MQTT_BROKER_PORT}",
        "mqtt_auth": "configured" if MQTT_USERNAME else "none",
        "mqtt_tls": MQTT_TLS_ENABLED,
        "storage_backend": STORAGE_BACKEND,
        "influxdb_enabled": INFLUXDB_ENABLED,
        "api_bind": f"{API_HOST}:{API_PORT}",
        "twin_time_step_sec": TWIN_TIME_STEP_SEC,
        "random_seed": RANDOM_SEED,
        "sensor_warn_pct": WATER_LEVEL_WARN_PCT,
        "sensor_crit_pct": WATER_LEVEL_CRIT_PCT,
        "sensor_hysteresis_pct": WATER_LEVEL_HYSTERESIS_PCT,
    }
