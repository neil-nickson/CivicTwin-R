"""
CivicTwin-R — MQTT Ingestion Service

Subscribes to telemetry topics, validates schemas, detects duplicates and stale
readings, evaluates threshold hysteresis, and persists to the active storage backend.
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Callable

from backend.models import (
    TelemetryMessage, IngestedTelemetry, SensorStatus,
    DataSource, Alert, AlertLevel,
)
from backend import config

logger = logging.getLogger("civictwin.ingestion")

STALE_TIMEOUT = timedelta(seconds=config.STALE_READING_TIMEOUT_SEC)


class IngestionService:
    """Validates, deduplicates, and processes incoming MQTT telemetry messages."""

    def __init__(self, storage):
        self._storage = storage
        self._seen_sequences: dict[str, int] = {}
        self._last_timestamps: dict[str, datetime] = {}
        self._zone_status: dict[str, dict] = {}
        self._live_zone_status: dict[str, dict] = {}
        self._zone_alert_states: dict[str, AlertLevel] = {}
        self._alerts: list[Alert] = []
        self._stats = {
            "messages_received": 0,
            "messages_valid": 0,
            "messages_invalid": 0,
            "messages_duplicate": 0,
            "messages_stale": 0,
            "messages_stored": 0,
        }
        self._callbacks: list[Callable] = []
        self._lock = threading.RLock()
        logger.info("IngestionService initialized")

    @property
    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)

    @property
    def alerts(self) -> list[Alert]:
        with self._lock:
            return list(self._alerts)

    @property
    def zone_status(self) -> dict:
        with self._lock:
            return dict(self._zone_status)

    @property
    def live_zone_status(self) -> dict:
        with self._lock:
            return dict(self._live_zone_status)

    def add_callback(self, cb: Callable):
        self._callbacks.append(cb)

    def process_raw(self, topic: str, payload: bytes) -> Optional[IngestedTelemetry]:
        with self._lock:
            self._stats["messages_received"] += 1

        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning("Malformed JSON payload on %s: %s", topic, e)
            with self._lock:
                self._stats["messages_invalid"] += 1
            return None

        return self.process_message(data)

    def process_message(self, data: dict) -> Optional[IngestedTelemetry]:
        ingestion_ts = datetime.now(timezone.utc)

        try:
            msg = TelemetryMessage(**data)
        except Exception as e:
            logger.warning("Schema validation failed: %s", e)
            with self._lock:
                self._stats["messages_invalid"] += 1
            return None

        device_key = msg.device_id
        is_duplicate = False
        with self._lock:
            last_seq = self._seen_sequences.get(device_key, -1)
            if msg.sequence <= last_seq:
                self._stats["messages_duplicate"] += 1
                is_duplicate = True
                logger.debug("Duplicate rejected: %s seq=%d (last=%d)", device_key, msg.sequence, last_seq)
            else:
                self._seen_sequences[device_key] = msg.sequence

        if is_duplicate:
            return None

        is_stale = False
        with self._lock:
            last_ts = self._last_timestamps.get(device_key)
            is_replay = msg.source in (DataSource.REPLAYED_HISTORICAL, "replayed_historical")
            if not is_replay and last_ts and (ingestion_ts - msg.timestamp) > STALE_TIMEOUT:
                is_stale = True
                self._stats["messages_stale"] += 1
            self._last_timestamps[device_key] = msg.timestamp

        quality_flags = list(msg.quality_flags)
        if is_stale:
            quality_flags.append("stale")
        if msg.sensor_status == SensorStatus.INVALID:
            quality_flags.append("invalid_reading")
        if msg.water_level_pct is not None and (msg.water_level_pct < 0 or msg.water_level_pct > 100):
            quality_flags.append("out_of_range")

        msg_data = msg.model_dump()
        msg_data.pop("quality_flags", None)
        record = IngestedTelemetry(
            **msg_data,
            ingestion_timestamp=ingestion_ts,
            is_duplicate=False,
            is_stale=is_stale,
            quality_flags=quality_flags,
        )

        stored = self._storage.store(record)
        with self._lock:
            self._stats["messages_valid"] += 1
            if stored:
                self._stats["messages_stored"] += 1

        self._check_thresholds(record)
        self._update_zone_status(record)

        for cb in self._callbacks:
            try:
                cb(record)
            except Exception:
                logger.exception("Ingestion callback error")

        return record

    def _update_zone_status(self, record: IngestedTelemetry):
        with self._lock:
            alert_level = self._zone_alert_states.get(record.zone_id, AlertLevel.NORMAL)
            status = {
                "zone_id": record.zone_id,
                "device_id": record.device_id,
                "sensor_status": record.sensor_status.value,
                "water_level_pct": record.water_level_pct,
                "water_level_cm": record.water_level_cm,
                "current_level_pct": record.water_level_pct,
                "current_level_cm": record.water_level_cm,
                "rain_sensor_raw": record.rain_sensor_raw,
                "rain_indicator_pct": record.rain_indicator_pct,
                "rainfall_measurement_type": (
                    "SIMULATED_UNCALIBRATED_INDICATOR"
                    if record.rain_indicator_pct is not None
                    else None
                ),
                "alert_level": alert_level.value,
                "source": record.source.value,
                "telemetry_timestamp": record.timestamp.isoformat(),
                "ingestion_timestamp": record.ingestion_timestamp.isoformat(),
                "last_update": record.timestamp.isoformat(),
                "is_stale": record.is_stale,
                "quality_flags": record.quality_flags,
            }
            self._zone_status[record.zone_id] = status
            if (
                config.LIVE_TELEMETRY_SOURCE
                and record.source.value == config.LIVE_TELEMETRY_SOURCE
            ):
                self._live_zone_status[record.zone_id] = status

    def _check_thresholds(self, record: IngestedTelemetry):
        if record.water_level_pct is None:
            return
        if record.sensor_status in (SensorStatus.INVALID, SensorStatus.OFFLINE):
            return

        pct = record.water_level_pct
        zone_id = record.zone_id
        warn_th = config.WATER_LEVEL_WARN_PCT
        crit_th = config.WATER_LEVEL_CRIT_PCT
        hyst = config.WATER_LEVEL_HYSTERESIS_PCT

        with self._lock:
            current_state = self._zone_alert_states.get(zone_id, AlertLevel.NORMAL)
            new_state = current_state

            if current_state == AlertLevel.NORMAL:
                if pct >= crit_th:
                    new_state = AlertLevel.CRITICAL
                elif pct >= warn_th:
                    new_state = AlertLevel.WARNING
            elif current_state == AlertLevel.WARNING:
                if pct >= crit_th:
                    new_state = AlertLevel.CRITICAL
                elif pct < (warn_th - hyst):
                    new_state = AlertLevel.NORMAL
            elif current_state == AlertLevel.CRITICAL:
                if pct < (crit_th - hyst):
                    if pct >= warn_th:
                        new_state = AlertLevel.WARNING
                    else:
                        new_state = AlertLevel.NORMAL

            self._zone_alert_states[zone_id] = new_state

            if new_state != current_state:
                alert = Alert(
                    alert_id=f"alert_{zone_id}_{record.sequence}",
                    zone_id=zone_id,
                    device_id=record.device_id,
                    alert_level=new_state,
                    water_level_pct=round(pct, 2),
                    threshold_pct=(
                        crit_th if new_state == AlertLevel.CRITICAL
                        else warn_th if new_state == AlertLevel.WARNING
                        else 0.0
                    ),
                    message=(
                        f"Zone {zone_id}: water level at {pct:.1f}% ({new_state.value})"
                        if new_state != AlertLevel.NORMAL
                        else f"Zone {zone_id}: water level normal ({pct:.1f}%)"
                    ),
                )
                self._alerts.append(alert)
                if len(self._alerts) > 100:
                    self._alerts = self._alerts[-100:]
                logger.warning("ALERT transition [%s]: %s -> %s (level=%.1f%%)",
                               zone_id, current_state.value, new_state.value, pct)

    def get_health(self) -> dict:
        return {
            "status": "running",
            "stats": self.stats,
            "zones_tracked": len(self._zone_status),
            "active_alerts": sum(1 for a in self._alerts if not a.acknowledged),
        }


class MQTTIngestionClient:
    """MQTT client subscribing to telemetry topics and routing to IngestionService."""

    def __init__(self, ingestion: IngestionService):
        self._ingestion = ingestion
        self._client = None
        self._connected = False
        self._topic_prefix = config.MQTT_TOPIC_PREFIX

    def _configure_tls(self, client) -> None:
        if not config.MQTT_TLS_ENABLED:
            return

        if config.MQTT_TLS_CA_FILE:
            tls_context = ssl.create_default_context(cafile=config.MQTT_TLS_CA_FILE)
        else:
            tls_context = ssl.create_default_context()
        client.tls_set_context(tls_context)

    def start(self) -> bool:
        try:
            import paho.mqtt.client as mqtt

            self._client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=config.MQTT_CLIENT_ID,
            )

            if config.MQTT_USERNAME:
                self._client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)

            self._configure_tls(self._client)
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            self._client.connect_async(config.MQTT_BROKER_HOST, config.MQTT_BROKER_PORT)
            self._client.loop_start()
            logger.info("MQTT client connecting to %s:%d", config.MQTT_BROKER_HOST, config.MQTT_BROKER_PORT)
            return True
        except Exception as e:
            logger.warning("MQTT client start failed: %s", e)
            return False

    def stop(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False
            logger.info("MQTT client stopped")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            self._connected = True
            topic = f"{self._topic_prefix}/zones/+/telemetry"
            client.subscribe(topic, qos=config.MQTT_QOS)
            logger.info("MQTT connected, subscribed to %s", topic)
        else:
            logger.error("MQTT connection failed with code %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._connected = False
        logger.warning("MQTT disconnected (rc=%s)", reason_code)

    def _on_message(self, client, userdata, msg):
        self._ingestion.process_raw(msg.topic, msg.payload)
