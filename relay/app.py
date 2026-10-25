"""Authenticated HTTPS relay for the public Adyar Wokwi simulation."""

from __future__ import annotations

import hmac
import json
import logging
import os
import ssl
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal, Optional

import paho.mqtt.client as mqtt
from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger("civictwin.relay")

MAX_PAYLOAD_BYTES = 16 * 1024
TELEMETRY_TOPIC = "civictwin/v1/zones/zone_01/telemetry"
EXPECTED_DEVICE_ID = "node_adyar_01"
EXPECTED_ZONE_ID = "zone_01"
EXPECTED_SOURCE = "WOKWI_SIMULATED"


class RelaySettings:
    """Environment-backed relay settings without exposing secret values."""

    broker_host: str = os.getenv(
        "RELAY_MQTT_BROKER_HOST",
        "wfde4d21.ala.asia-southeast1.emqxsl.com",
    )
    broker_port: int = int(os.getenv("RELAY_MQTT_BROKER_PORT", "8883"))
    mqtt_username: str = os.getenv("RELAY_MQTT_USERNAME", "")
    mqtt_password: str = os.getenv("RELAY_MQTT_PASSWORD", "")
    mqtt_client_id: str = os.getenv(
        "RELAY_MQTT_CLIENT_ID", "civictwin_adyar_relay_01"
    )
    ingest_token: str = os.getenv("WOKWI_ADYAR_INGEST_TOKEN", "")


class RelayTelemetry(BaseModel):
    """Only the canonical Adyar Wokwi telemetry identity is accepted."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    device_id: Literal["node_adyar_01"]
    zone_id: Literal["zone_01"]
    sequence: int = Field(..., ge=0)
    timestamp: datetime
    distance_cm: Optional[float] = None
    water_level_cm: Optional[float] = None
    water_level_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    rain_sensor_raw: Optional[int] = Field(default=None, ge=0, le=4095)
    rain_indicator_pct: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    sensor_status: Literal["valid", "invalid", "stale", "offline", "simulated"] = "valid"
    source: Literal["WOKWI_SIMULATED"]
    quality_flags: list[str] = Field(default_factory=list)


class MqttRelay:
    """Owns the relay's authenticated, certificate-verified MQTT connection."""

    def __init__(self, settings: RelaySettings):
        self._settings = settings
        self._client: Optional[mqtt.Client] = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def start(self) -> None:
        if not self._settings.mqtt_username or not self._settings.mqtt_password:
            logger.error("Relay MQTT credentials are not configured")
            return

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._settings.mqtt_client_id,
        )
        client.username_pw_set(
            self._settings.mqtt_username,
            self._settings.mqtt_password,
        )
        client.tls_set_context(ssl.create_default_context())
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        self._client = client

        try:
            client.connect_async(self._settings.broker_host, self._settings.broker_port)
            client.loop_start()
        except (OSError, ValueError):
            logger.exception("Relay MQTT connection setup failed")

    def stop(self) -> None:
        client = self._client
        if client is not None:
            client.loop_stop()
            client.disconnect()
        self._connected = False

    def publish(self, payload: bytes) -> bool:
        client = self._client
        if client is None or not self._connected:
            return False
        result = client.publish(TELEMETRY_TOPIC, payload, qos=1)
        return result.rc == mqtt.MQTT_ERR_SUCCESS

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        self._connected = reason_code == 0
        if not self._connected:
            logger.error("Relay MQTT connection failed with code %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._connected = False


settings = RelaySettings()
relay = MqttRelay(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    relay.start()
    yield
    relay.stop()


app = FastAPI(
    title="CivicTwin-R Adyar Wokwi Relay",
    version="1.0.0",
    lifespan=lifespan,
)


def _authorize(authorization: Optional[str]) -> None:
    expected = settings.ingest_token
    supplied = ""
    if authorization and authorization.startswith("Bearer "):
        supplied = authorization[7:]
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
        )


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "mqtt_connected": relay.connected,
        "accepted_zone": EXPECTED_ZONE_ID,
        "accepted_source": EXPECTED_SOURCE,
    }


@app.post("/api/wokwi/adyar/telemetry", status_code=status.HTTP_202_ACCEPTED)
async def receive_adyar_telemetry(
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    _authorize(authorization)

    content_length = request.headers.get("content-length")
    if content_length and (
        not content_length.isdigit() or int(content_length) > MAX_PAYLOAD_BYTES
    ):
        raise HTTPException(status_code=413, detail="Payload too large")

    body = await request.body()
    if len(body) > MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    try:
        payload = RelayTelemetry.model_validate_json(body)
    except (ValidationError, UnicodeDecodeError):
        raise HTTPException(status_code=422, detail="Invalid telemetry payload")

    if not relay.publish(
        json.dumps(
            payload.model_dump(mode="json"),
            separators=(",", ":"),
        ).encode("utf-8")
    ):
        raise HTTPException(status_code=503, detail="MQTT relay unavailable")

    return {"accepted": True, "topic": TELEMETRY_TOPIC}
