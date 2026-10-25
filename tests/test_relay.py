import json

from fastapi.testclient import TestClient

from relay import app as relay_module


def telemetry_payload():
    return {
        "schema_version": "1.0",
        "device_id": "node_adyar_01",
        "zone_id": "zone_01",
        "sequence": 1,
        "timestamp": "2026-10-25T12:00:00Z",
        "water_level_cm": 5.0,
        "water_level_pct": 16.67,
        "rain_sensor_raw": 1000,
        "rain_indicator_pct": 24.42,
        "sensor_status": "valid",
        "source": "WOKWI_SIMULATED",
        "quality_flags": ["rain_indicator_uncalibrated"],
    }


class FakeRelay:
    connected = True

    def __init__(self):
        self.topic = None
        self.payload = None

    def start(self):
        pass

    def stop(self):
        pass

    def publish(self, payload):
        self.topic = relay_module.TELEMETRY_TOPIC
        self.payload = payload
        return True


def client(monkeypatch):
    monkeypatch.setattr(relay_module.settings, "ingest_token", "test-token")
    fake = FakeRelay()
    monkeypatch.setattr(relay_module, "relay", fake)
    return TestClient(relay_module.app), fake


def test_health_reveals_no_credentials(monkeypatch):
    http, _ = client(monkeypatch)

    response = http.get("/health")

    assert response.status_code == 200
    assert "password" not in response.text.lower()
    assert "token" not in response.text.lower()


def test_rejects_missing_or_invalid_token(monkeypatch):
    http, _ = client(monkeypatch)
    payload = telemetry_payload()

    assert http.post("/api/wokwi/adyar/telemetry", json=payload).status_code == 401
    assert http.post(
        "/api/wokwi/adyar/telemetry",
        json=payload,
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 401


def test_valid_payload_publishes_fixed_topic(monkeypatch):
    http, fake = client(monkeypatch)

    response = http.post(
        "/api/wokwi/adyar/telemetry",
        json=telemetry_payload(),
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 202
    assert fake.topic == "civictwin/v1/zones/zone_01/telemetry"
    assert json.loads(fake.payload)["source"] == "WOKWI_SIMULATED"


def test_rejects_wrong_identity_and_extra_fields(monkeypatch):
    http, fake = client(monkeypatch)
    headers = {"Authorization": "Bearer test-token"}

    wrong_zone = telemetry_payload()
    wrong_zone["zone_id"] = "zone_02"
    assert http.post(
        "/api/wokwi/adyar/telemetry", json=wrong_zone, headers=headers
    ).status_code == 422

    extra = telemetry_payload()
    extra["topic"] = "civictwin/v1/zones/zone_02/telemetry"
    assert http.post(
        "/api/wokwi/adyar/telemetry", json=extra, headers=headers
    ).status_code == 422
    assert fake.payload is None


def test_mqtt_setup_uses_verified_tls(monkeypatch):
    created = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            created["client_id"] = kwargs["client_id"]

        def username_pw_set(self, username, password):
            created["credentials"] = (username, password)

        def tls_set_context(self, context):
            created["tls_context"] = context

        def connect_async(self, host, port):
            created["endpoint"] = (host, port)

        def loop_start(self):
            pass

    monkeypatch.setattr(relay_module.mqtt, "Client", FakeClient)
    monkeypatch.setattr(
        relay_module.settings,
        "mqtt_username",
        "relay-user",
    )
    monkeypatch.setattr(relay_module.settings, "mqtt_password", "relay-password")
    monkeypatch.setattr(relay_module.settings, "mqtt_client_id", "relay-test")

    publisher = relay_module.MqttRelay(relay_module.settings)
    publisher.start()

    assert created["endpoint"] == (
        "wfde4d21.ala.asia-southeast1.emqxsl.com",
        8883,
    )
    assert created["client_id"] == "relay-test"
    assert created["credentials"] == ("relay-user", "relay-password")
    assert created["tls_context"].verify_mode == relay_module.ssl.CERT_REQUIRED
    assert created["tls_context"].check_hostname is True
