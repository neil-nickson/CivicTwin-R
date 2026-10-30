"""
CivicTwin-R — Comprehensive Automated Test Suite

Tests digital twin physics, scenario controller, reconstruction algorithms,
data models, storage backends, ingestion pipeline, threshold hysteresis,
failure recovery, and FastAPI REST endpoints.
"""

from __future__ import annotations

import json
import math
import sqlite3
import ssl
import sys
import tempfile
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from digital_twin.model import ZoneModel, DigitalTwinEngine, ZoneReading, create_default_zones
from digital_twin.scenarios import ScenarioController, SCENARIOS, list_scenarios
from digital_twin.reconstruction import (
    locf,
    linear_interpolation,
    create_missing_mask,
    apply_mask,
    evaluate_reconstruction,
    run_reconstruction_experiment,
    run_multi_rate_experiment,
)
from backend.models import (
    TelemetryMessage, IngestedTelemetry, SensorStatus,
    DataSource, AlertLevel, ZoneConfig, ZoneState,
)
from backend.storage import MemoryStorage, SQLiteStorage, create_storage
from backend.ingestion import IngestionService, MQTTIngestionClient
from backend.app.main import app, _apply_dashboard_status
from backend import config


# Zone Model Tests

class TestZoneModel:
    def test_initial_conditions(self):
        zone = ZoneModel(zone_id="test", zone_name="Test Zone", baseline_level_cm=10.0)
        assert zone.current_level_cm == 10.0
        assert zone.step_count == 0

    def test_step_produces_reading(self):
        zone = ZoneModel(zone_id="test", zone_name="Test Zone")
        reading = zone.step(dt_sec=5.0)
        assert isinstance(reading, ZoneReading)
        assert reading.zone_id == "test"
        assert reading.step == 1
        assert reading.true_level_cm >= 0

    def test_drainage_reduces_level(self):
        zone = ZoneModel(
            zone_id="test", zone_name="Test",
            baseline_level_cm=20.0,
            inflow_rate=0.0,
            drainage_capacity=1.0,
        )
        zone.step(dt_sec=5.0)
        assert zone.current_level_cm < 20.0

    def test_inflow_increases_level(self):
        zone = ZoneModel(
            zone_id="test", zone_name="Test",
            baseline_level_cm=5.0,
            inflow_rate=2.0,
            drainage_capacity=0.0,
        )
        zone.step(dt_sec=5.0)
        assert zone.current_level_cm > 5.0

    def test_level_clamped_at_zero(self):
        zone = ZoneModel(
            zone_id="test", zone_name="Test",
            baseline_level_cm=0.5,
            inflow_rate=0.0,
            drainage_capacity=10.0,
        )
        zone.step(dt_sec=5.0)
        assert zone.current_level_cm >= 0.0

    def test_level_clamped_at_max(self):
        zone = ZoneModel(
            zone_id="test", zone_name="Test",
            container_height_cm=30.0,
            baseline_level_cm=29.0,
            inflow_rate=5.0,
            drainage_capacity=0.0,
        )
        zone.step(dt_sec=5.0)
        assert zone.current_level_cm <= 30.0

    def test_sensor_failure_simulation(self):
        zone = ZoneModel(
            zone_id="test", zone_name="Test",
            sensor_reliability=0.0,
        )
        reading = zone.step()
        assert not reading.sensor_ok
        assert reading.observed_level_cm is None
        assert reading.sensor_status == "invalid"

    def test_network_failure_simulation(self):
        zone = ZoneModel(
            zone_id="test", zone_name="Test",
            network_availability=0.0,
        )
        reading = zone.step()
        assert not reading.network_ok
        assert reading.sensor_status == "offline"

    def test_reset(self):
        zone = ZoneModel(
            zone_id="test", zone_name="Test",
            baseline_level_cm=10.0,
            inflow_rate=5.0,
            drainage_capacity=0.0,
        )
        zone.step()
        assert zone.current_level_cm > 10.0
        zone.reset()
        assert zone.current_level_cm == 10.0
        assert zone.step_count == 0

    def test_alert_levels(self):
        zone = ZoneModel(
            zone_id="test", zone_name="Test",
            container_height_cm=100.0,
            baseline_level_cm=10.0,
            warn_threshold_pct=70.0,
            crit_threshold_pct=85.0,
        )
        r1 = zone.step()
        assert r1.alert_level == "normal"

        zone.current_level_cm = 75.0
        r2 = zone.step(dt_sec=0.0)
        assert r2.alert_level == "warning"

        zone.current_level_cm = 90.0
        r3 = zone.step(dt_sec=0.0)
        assert r3.alert_level == "critical"


# Digital Twin Engine Tests

class TestDigitalTwinEngine:
    def test_add_zones(self):
        engine = DigitalTwinEngine(seed=42)
        zones = create_default_zones()
        for z in zones:
            engine.add_zone(z)
        assert len(engine.zones) == 3

    def test_step_all(self):
        engine = DigitalTwinEngine(seed=42)
        for z in create_default_zones():
            engine.add_zone(z)
        readings = engine.step_all(dt_sec=5.0)
        assert len(readings) == 3
        assert engine.global_step == 1

    def test_deterministic(self):
        e1 = DigitalTwinEngine(seed=123)
        e2 = DigitalTwinEngine(seed=123)
        for z in create_default_zones():
            e1.add_zone(z)
        for z in create_default_zones():
            e2.add_zone(z)

        r1 = e1.step_all()
        r2 = e2.step_all()

        for a, b in zip(r1, r2):
            assert a.true_level_cm == b.true_level_cm
            assert a.observed_level_cm == b.observed_level_cm

    def test_get_zone_state(self):
        engine = DigitalTwinEngine(seed=42)
        engine.add_zone(ZoneModel(zone_id="z1", zone_name="Zone 1", baseline_level_cm=10.0))
        state = engine.get_zone_state("z1")
        assert state is not None
        assert state["zone_id"] == "z1"
        assert state["current_level_cm"] == 10.0

    def test_apply_scenario_params(self):
        engine = DigitalTwinEngine(seed=42)
        zone = ZoneModel(zone_id="z1", zone_name="Zone 1", inflow_rate=0.1)
        engine.add_zone(zone)
        engine.apply_scenario_params("z1", inflow_rate=2.5)
        assert engine.zones["z1"].inflow_rate == 2.5


# Scenario Tests

class TestScenarios:
    def test_all_scenarios_defined(self):
        expected = [
            "normal", "heavy_rainfall", "blocked_drain", "sudden_rise",
            "sensor_dropout", "noisy_sensor", "network_outage", "multi_zone_stress",
        ]
        for name in expected:
            assert name in SCENARIOS, f"Missing scenario: {name}"

    def test_list_scenarios(self):
        scenarios = list_scenarios()
        assert len(scenarios) == 8
        for s in scenarios:
            assert "scenario_id" in s
            assert "name" in s
            assert "total_steps" in s

    def test_scenario_controller_load(self):
        engine = DigitalTwinEngine(seed=42)
        for z in create_default_zones():
            engine.add_zone(z)
        ctrl = ScenarioController(engine)
        assert ctrl.load_scenario("heavy_rainfall")
        assert ctrl.is_running
        assert not ctrl.is_complete
        assert ctrl.current_step == 0

    def test_scenario_runs_to_completion(self):
        engine = DigitalTwinEngine(seed=42)
        for z in create_default_zones():
            engine.add_zone(z)
        ctrl = ScenarioController(engine)
        ctrl.load_scenario("normal")
        steps = 0
        while not ctrl.is_complete:
            data = ctrl.step()
            steps += 1
            assert len(data) == 3
        assert steps == 200
        assert not ctrl.is_running
        assert ctrl.is_complete

    def test_scenario_stop(self):
        engine = DigitalTwinEngine(seed=42)
        for z in create_default_zones():
            engine.add_zone(z)
        ctrl = ScenarioController(engine)
        ctrl.load_scenario("heavy_rainfall")
        ctrl.step()
        ctrl.step()
        ctrl.stop()
        assert not ctrl.is_running

    def test_invalid_scenario(self):
        engine = DigitalTwinEngine(seed=42)
        ctrl = ScenarioController(engine)
        assert not ctrl.load_scenario("nonexistent_scenario")


# Reconstruction Tests

class TestReconstruction:
    def test_locf_basic(self):
        series = [1.0, None, None, 4.0, None]
        result = locf(series)
        assert result == [1.0, 1.0, 1.0, 4.0, 4.0]

    def test_locf_leading_none(self):
        series = [None, None, 3.0, None]
        result = locf(series)
        assert result == [None, None, 3.0, 3.0]

    def test_linear_interpolation_basic(self):
        series = [10.0, None, None, 40.0]
        result = linear_interpolation(series)
        assert result[0] == 10.0
        assert abs(result[1] - 20.0) < 1e-6
        assert abs(result[2] - 30.0) < 1e-6
        assert result[3] == 40.0

    def test_linear_interpolation_no_gaps(self):
        series = [1.0, 2.0, 3.0]
        result = linear_interpolation(series)
        assert result == [1.0, 2.0, 3.0]

    def test_mask_creation(self):
        mask = create_missing_mask(100, missing_rate=0.2, seed=42)
        assert len(mask) == 100
        assert any(mask)
        assert not all(mask)
        assert abs(sum(mask) - 20) <= 5

    def test_mask_deterministic(self):
        m1 = create_missing_mask(100, 0.2, seed=99)
        m2 = create_missing_mask(100, 0.2, seed=99)
        assert m1 == m2

    def test_apply_mask(self):
        gt = [1.0, 2.0, 3.0, 4.0, 5.0]
        mask = [False, True, False, True, False]
        masked = apply_mask(gt, mask)
        assert masked == [1.0, None, 3.0, None, 5.0]

    def test_evaluate_reconstruction_metrics(self):
        gt = [10.0, 20.0, 30.0, 40.0, 50.0]
        recon = [10.0, 22.0, 30.0, 38.0, 50.0]
        mask = [False, True, False, True, False]
        metrics = evaluate_reconstruction(gt, recon, mask)
        assert metrics["evaluated_points"] == 2
        assert abs(metrics["mae"] - 2.0) < 1e-4
        assert abs(metrics["rmse"] - 2.0) < 1e-4
        assert abs(metrics["max_error"] - 2.0) < 1e-4

    def test_no_data_leakage(self):
        gt = [10.0, 15.0, 20.0, 25.0, 30.0]
        mask = [False, True, True, True, False]
        masked = apply_mask(gt, mask)
        recon_locf = locf(masked)
        assert recon_locf[1] == 10.0
        assert recon_locf[2] == 10.0
        assert recon_locf[3] == 10.0

    def test_run_experiment(self):
        gt = [math.sin(i * 0.1) * 10 + 20 for i in range(100)]
        results = run_reconstruction_experiment(gt, missing_rate=0.1, seed=42)
        assert len(results) == 2
        methods = {r.method for r in results}
        assert "locf" in methods
        assert "linear" in methods
        for r in results:
            assert r.mae >= 0
            assert r.rmse >= 0

    def test_multi_rate_experiment(self):
        gt = [10.0 + i * 0.5 for i in range(100)]
        results = run_multi_rate_experiment(gt, rates=[0.1, 0.2], seed=42)
        assert len(results) == 4


# Data Model Tests

class TestModels:
    def test_telemetry_valid(self):
        msg = TelemetryMessage(
            device_id="dev1",
            zone_id="zone_01",
            sequence=1,
            timestamp=datetime.now(timezone.utc),
            water_level_cm=15.0,
            water_level_pct=50.0,
        )
        assert msg.device_id == "dev1"
        assert msg.water_level_pct == 50.0

    def test_telemetry_pct_clamped(self):
        msg = TelemetryMessage(
            device_id="dev1",
            zone_id="zone_01",
            sequence=1,
            timestamp=datetime.now(timezone.utc),
            water_level_pct=150.0,
        )
        assert msg.water_level_pct == 100.0

    def test_optional_rain_indicator_fields(self):
        msg = TelemetryMessage(
            device_id="rain_node",
            zone_id="zone_01",
            sequence=1,
            timestamp=datetime.now(timezone.utc),
            rain_sensor_raw=2048,
            rain_indicator_pct=50.0,
            source=DataSource.WOKWI_SIMULATED,
        )
        assert msg.rain_sensor_raw == 2048
        assert msg.rain_indicator_pct == 50.0

    def test_telemetry_without_rain_indicator_remains_compatible(self):
        msg = TelemetryMessage(
            device_id="legacy_node",
            zone_id="zone_01",
            sequence=1,
            timestamp=datetime.now(timezone.utc),
        )
        assert msg.rain_sensor_raw is None
        assert msg.rain_indicator_pct is None

    def test_telemetry_iso_timestamp(self):
        msg = TelemetryMessage(
            device_id="dev1",
            zone_id="zone_01",
            sequence=1,
            timestamp="2026-10-23T12:00:00Z",
            water_level_pct=50.0,
        )
        assert isinstance(msg.timestamp, datetime)

    def test_telemetry_invalid_device_id(self):
        with pytest.raises(Exception):
            TelemetryMessage(
                device_id="",
                zone_id="zone_01",
                sequence=1,
                timestamp=datetime.now(timezone.utc),
            )


# Storage Tests

class TestStorage:
    def _make_record(self, zone_id="z1", seq=1, level=10.0, pct=33.3):
        return IngestedTelemetry(
            device_id=f"dev_{zone_id}",
            zone_id=zone_id,
            sequence=seq,
            timestamp=datetime.now(timezone.utc),
            water_level_cm=level,
            water_level_pct=pct,
            sensor_status=SensorStatus.VALID,
            source=DataSource.SIMULATOR,
        )

    def test_memory_store_and_query(self):
        store = MemoryStorage()
        rec = self._make_record("z1", 1)
        assert store.store(rec)
        assert store.count() == 1
        latest = store.query_latest("z1")
        assert len(latest) == 1
        assert latest[0]["sequence"] == 1

    def test_memory_duplicate_rejection(self):
        store = MemoryStorage()
        rec = self._make_record("z1", 1)
        assert store.store(rec)
        assert not store.store(rec)
        assert store.count() == 1

    def test_sqlite_store_and_query(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            store = SQLiteStorage(tmp.name)
            rec = self._make_record("z1", 1)
            assert store.store(rec)
            assert store.count() == 1
            latest = store.query_latest("z1")
            assert len(latest) == 1
            assert latest[0]["sequence"] == 1
            store.close()

    def test_sqlite_store_and_query_rain_indicator(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            store = SQLiteStorage(tmp.name)
            rec = IngestedTelemetry(
                device_id="rain_node",
                zone_id="zone_01",
                sequence=1,
                timestamp=datetime.now(timezone.utc),
                rain_sensor_raw=3000,
                rain_indicator_pct=73.26,
                source=DataSource.WOKWI_SIMULATED,
            )
            assert store.store(rec)
            latest = store.query_latest("zone_01")
            assert latest[0]["rain_sensor_raw"] == 3000
            assert latest[0]["rain_indicator_pct"] == 73.26
            store.close()

    def test_sqlite_migrates_existing_table_without_data_loss(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            conn = sqlite3.connect(tmp.name)
            conn.execute(
                """CREATE TABLE telemetry (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   device_id TEXT NOT NULL,
                   zone_id TEXT NOT NULL,
                   sequence INTEGER NOT NULL,
                   event_timestamp TEXT NOT NULL,
                   ingestion_timestamp TEXT NOT NULL,
                   distance_cm REAL,
                   water_level_cm REAL,
                   water_level_pct REAL,
                   sensor_status TEXT NOT NULL,
                   source TEXT NOT NULL,
                   is_reconstructed INTEGER DEFAULT 0,
                   reconstruction_method TEXT,
                   scenario_id TEXT,
                   quality_flags TEXT DEFAULT '[]',
                   UNIQUE(device_id, sequence)
                )"""
            )
            conn.execute(
                """INSERT INTO telemetry
                   (device_id, zone_id, sequence, event_timestamp,
                    ingestion_timestamp, water_level_cm, water_level_pct,
                    sensor_status, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "legacy_node",
                    "zone_01",
                    1,
                    "2026-10-24T10:00:00+00:00",
                    "2026-10-24T10:00:01+00:00",
                    10.0,
                    33.3,
                    "valid",
                    "PYTHON_SIMULATED",
                ),
            )
            conn.commit()
            conn.close()

            store = SQLiteStorage(tmp.name)
            columns = {
                row["name"]
                for row in store._conn.execute("PRAGMA table_info(telemetry)").fetchall()
            }
            assert {"rain_sensor_raw", "rain_indicator_pct"} <= columns
            latest = store.query_latest("zone_01")
            assert latest[0]["device_id"] == "legacy_node"
            assert latest[0]["rain_sensor_raw"] is None
            store.close()

    def test_sqlite_duplicate_rejection(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            store = SQLiteStorage(tmp.name)
            rec = self._make_record("z1", 1)
            assert store.store(rec)
            store.store(rec)
            assert store.count() == 1
            store.close()

    def test_query_all_latest(self):
        store = MemoryStorage()
        store.store(self._make_record("z1", 1))
        store.store(self._make_record("z2", 2))
        all_latest = store.query_all_latest()
        assert "z1" in all_latest
        assert "z2" in all_latest

    def test_storage_fallback_on_unconfigured_influx(self, monkeypatch):
        monkeypatch.setattr(config, "INFLUXDB_ENABLED", False)
        store = create_storage("influxdb")
        assert isinstance(store, SQLiteStorage)
        store.close()


# Ingestion Pipeline Tests

class TestIngestion:
    def test_process_valid_message(self):
        store = MemoryStorage()
        svc = IngestionService(store)
        msg = {
            "device_id": "dev1",
            "zone_id": "zone_01",
            "sequence": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "water_level_cm": 15.0,
            "water_level_pct": 50.0,
            "sensor_status": "valid",
            "source": "simulator",
        }
        result = svc.process_message(msg)
        assert result is not None
        assert svc.stats["messages_valid"] == 1

    def test_reject_invalid_message(self):
        store = MemoryStorage()
        svc = IngestionService(store)
        result = svc.process_message({"bad": "data"})
        assert result is None
        assert svc.stats["messages_invalid"] == 1

    def test_duplicate_detection(self):
        store = MemoryStorage()
        svc = IngestionService(store)
        msg = {
            "device_id": "dev1",
            "zone_id": "zone_01",
            "sequence": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "water_level_pct": 50.0,
        }
        svc.process_message(msg)
        result = svc.process_message(msg)
        assert result is None
        assert svc.stats["messages_duplicate"] == 1

    def test_threshold_alert_generation(self):
        store = MemoryStorage()
        svc = IngestionService(store)
        msg = {
            "device_id": "dev1",
            "zone_id": "zone_01",
            "sequence": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "water_level_pct": 90.0,
            "sensor_status": "valid",
        }
        svc.process_message(msg)
        alerts = svc.alerts
        assert len(alerts) >= 1
        assert alerts[-1].alert_level == AlertLevel.CRITICAL

    def test_health_endpoint(self):
        store = MemoryStorage()
        svc = IngestionService(store)
        health = svc.get_health()
        assert health["status"] == "running"
        assert "stats" in health

    def test_raw_mqtt_processing(self):
        store = MemoryStorage()
        svc = IngestionService(store)
        payload = json.dumps({
            "device_id": "mqtt_dev",
            "zone_id": "zone_01",
            "sequence": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "water_level_pct": 40.0,
        }).encode("utf-8")
        rec = svc.process_raw("civictwin/v1/zones/zone_01/telemetry", payload)
        assert rec is not None
        assert rec.device_id == "mqtt_dev"


# Threshold Hysteresis Tests

class TestHysteresisAndAlerts:
    def test_hysteresis_prevents_chatter(self):
        store = MemoryStorage()
        svc = IngestionService(store)
        base_time = datetime.now(timezone.utc)

        # Baseline reading: normal
        svc.process_message({
            "device_id": "d1",
            "zone_id": "zone_01",
            "sequence": 1,
            "timestamp": base_time.isoformat(),
            "water_level_pct": 60.0,
        })
        assert len(svc.alerts) == 0

        # Rises to 71.0% (crosses warning threshold of 70%)
        svc.process_message({
            "device_id": "d1",
            "zone_id": "zone_01",
            "sequence": 2,
            "timestamp": (base_time + timedelta(seconds=5)).isoformat(),
            "water_level_pct": 71.0,
        })
        assert len(svc.alerts) == 1
        assert svc.alerts[-1].alert_level == AlertLevel.WARNING

        # Dips slightly to 69.0% (inside hysteresis band: 70 - 3 = 67%)
        svc.process_message({
            "device_id": "d1",
            "zone_id": "zone_01",
            "sequence": 3,
            "timestamp": (base_time + timedelta(seconds=10)).isoformat(),
            "water_level_pct": 69.0,
        })
        # Should stay in warning; no clearing event yet
        assert len(svc.alerts) == 1

        # Rises back to 71.0% - should not create duplicate warning
        svc.process_message({
            "device_id": "d1",
            "zone_id": "zone_01",
            "sequence": 4,
            "timestamp": (base_time + timedelta(seconds=15)).isoformat(),
            "water_level_pct": 71.0,
        })
        assert len(svc.alerts) == 1

        # Drops below clearing threshold (65% < 67%)
        svc.process_message({
            "device_id": "d1",
            "zone_id": "zone_01",
            "sequence": 5,
            "timestamp": (base_time + timedelta(seconds=20)).isoformat(),
            "water_level_pct": 65.0,
        })
        # Alert clears to normal
        assert len(svc.alerts) == 2
        assert svc.alerts[-1].alert_level == AlertLevel.NORMAL


# Failure and Recovery Tests

class TestFailureAndRecovery:
    def test_sensor_dropout_and_recovery(self):
        zone = ZoneModel(zone_id="dropout_zone", zone_name="Dropout Test", baseline_level_cm=10.0)

        r1 = zone.step()
        assert r1.sensor_ok
        assert r1.sensor_status == "valid"

        # Inject failure
        zone.sensor_reliability = 0.0
        r2 = zone.step()
        assert not r2.sensor_ok
        assert r2.sensor_status == "invalid"
        assert r2.observed_level_cm is None

        # Recovery
        zone.sensor_reliability = 1.0
        r3 = zone.step()
        assert r3.sensor_ok
        assert r3.sensor_status == "valid"
        assert r3.observed_level_cm is not None

    def test_network_outage_and_recovery(self):
        zone = ZoneModel(zone_id="net_zone", zone_name="Network Test", baseline_level_cm=10.0)

        # Normal transmission
        r1 = zone.step()
        assert r1.network_ok
        assert r1.sensor_status == "valid"

        # Outage
        zone.network_availability = 0.0
        r2 = zone.step()
        assert not r2.network_ok
        assert r2.sensor_status == "offline"

        # Restoration
        zone.network_availability = 1.0
        r3 = zone.step()
        assert r3.network_ok
        assert r3.sensor_status == "valid"


# Integration Tests

class TestIntegration:
    def test_simulator_to_ingestion_pipeline(self):
        from backend.simulator import SensorSimulator

        store = MemoryStorage()
        svc = IngestionService(store)
        engine = DigitalTwinEngine(seed=42)
        for z in create_default_zones():
            engine.add_zone(z)

        sim = SensorSimulator(
            engine=engine,
            direct_callback=svc.process_message,
        )

        for _ in range(10):
            messages = sim.generate_one_step()
            for msg in messages:
                if msg.get("sensor_status") != "offline":
                    svc.process_message(msg)

        assert store.count() > 0
        assert svc.stats["messages_valid"] > 0

    def test_scenario_produces_data(self):
        engine = DigitalTwinEngine(seed=42)
        for z in create_default_zones():
            engine.add_zone(z)

        ctrl = ScenarioController(engine)
        ctrl.load_scenario("heavy_rainfall")

        max_level = 0
        steps = 0
        while not ctrl.is_complete:
            data = ctrl.step(dt_sec=5.0)
            steps += 1
            for d in data:
                if d["zone_id"] == "zone_01":
                    max_level = max(max_level, d["true_level_pct"])

        assert max_level > 50.0
        assert steps == SCENARIOS["heavy_rainfall"].total_steps


# REST API Endpoints Tests

@pytest.fixture(scope="module")
def api_client():
    with TestClient(app) as test_client:
        yield test_client


class TestApiEndpoints:
    def test_health_endpoint(self, api_client):
        res = api_client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "healthy"
        assert "storage" in data

    def test_config_endpoint(self, api_client):
        res = api_client.get("/api/config")
        assert res.status_code == 200
        data = res.json()
        assert "mqtt_broker" in data
        assert "sensor_warn_pct" in data

    def test_get_zones(self, api_client):
        res = api_client.get("/api/zones")
        assert res.status_code == 200
        data = res.json()
        assert "zones" in data
        assert len(data["zones"]) == 3
        z1 = data["zones"][0]
        assert "water_level_cm" in z1
        assert "water_level_pct" in z1
        assert "alert_level" in z1

    def test_get_single_zone(self, api_client):
        res = api_client.get("/api/zones/zone_01")
        assert res.status_code == 200
        assert res.json()["zone_id"] == "zone_01"

        res_404 = api_client.get("/api/zones/zone_nonexistent")
        assert res_404.status_code == 404

    def test_scenarios_listing(self, api_client):
        res = api_client.get("/api/scenarios")
        assert res.status_code == 200
        scenarios = res.json()["scenarios"]
        assert len(scenarios) == 8

    def test_scenario_execution_lifecycle(self, api_client):
        res_stat = api_client.get("/api/scenarios/status")
        assert res_stat.status_code == 200

        res_start = api_client.post("/api/scenarios/blocked_drain/start")
        assert res_start.status_code == 200
        assert res_start.json()["status"] == "started"

        res_step = api_client.post("/api/scenarios/step?steps=5")
        assert res_step.status_code == 200
        assert res_step.json()["data_points"] > 0

        res_stop = api_client.post("/api/scenarios/blocked_drain/stop")
        assert res_stop.status_code == 200
        assert res_stop.json()["status"] == "stopped"

    def test_telemetry_latest_and_history(self, api_client):
        res_latest = api_client.get("/api/telemetry/latest")
        assert res_latest.status_code == 200

        res_hist = api_client.get("/api/telemetry/history?zone_id=zone_01&minutes=10")
        assert res_hist.status_code == 200
        assert "records" in res_hist.json()

    def test_alerts_endpoint(self, api_client):
        res = api_client.get("/api/alerts")
        assert res.status_code == 200
        assert "alerts" in res.json()

    def test_stats_endpoint(self, api_client):
        res = api_client.get("/api/stats")
        assert res.status_code == 200
        data = res.json()
        assert "storage" in data
        assert "ingestion" in data

    def test_data_export(self, api_client):
        res_json = api_client.get("/api/data/export?format=json")
        assert res_json.status_code == 200
        assert "records" in res_json.json()

        res_csv = api_client.get("/api/data/export?format=csv")
        assert res_csv.status_code == 200
        assert res_csv.json()["status"] == "exported"


class TestProvenanceAndSensorVerification:
    """Verifies data provenance standards, sensor math, invalid handling, and thresholds."""

    def test_all_canonical_provenance_labels(self):
        """Verify all 6 canonical provenance tags are valid and preserved."""
        canonical_tags = [
            DataSource.WOKWI_SIMULATED,
            DataSource.PYTHON_SIMULATED,
            DataSource.OBSERVED,
            DataSource.REPLAYED_HISTORICAL,
            DataSource.DIGITAL_TWIN,
            DataSource.IMPUTED,
        ]
        assert len(canonical_tags) == 6

        storage = MemoryStorage()
        ingestion = IngestionService(storage=storage)
        base_time = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)

        for idx, tag in enumerate(canonical_tags, start=1):
            msg = TelemetryMessage(
                device_id=f"device_{idx}",
                zone_id="zone_01",
                sequence=idx,
                timestamp=base_time + timedelta(seconds=idx * 5),
                distance_cm=10.0,
                water_level_cm=20.0,
                water_level_pct=66.7,
                sensor_status=SensorStatus.VALID,
                source=tag,
            )
            assert msg.source == tag
            record = ingestion.process_message(msg.model_dump())
            assert record is not None
            assert record.source == tag

        # Verify storage records retain exact source tags
        stored_records = storage.query_latest("zone_01", limit=10)
        stored_sources = {r["source"] for r in stored_records}
        for tag in canonical_tags:
            assert tag.value in stored_sources

    def test_live_zone_status_accepts_only_configured_wokwi_source(self, monkeypatch):
        monkeypatch.setattr(config, "LIVE_TELEMETRY_SOURCE", "WOKWI_SIMULATED")
        storage = MemoryStorage()
        ingestion = IngestionService(storage=storage)
        timestamp = datetime.now(timezone.utc)

        ingestion.process_message({
            "device_id": "python_zone_01", "zone_id": "zone_01", "sequence": 1,
            "timestamp": timestamp.isoformat(), "water_level_pct": 20.0,
            "source": "PYTHON_SIMULATED",
        })
        assert ingestion.live_zone_status == {}

        ingestion.process_message({
            "device_id": "node_adyar_01", "zone_id": "zone_01", "sequence": 1,
            "timestamp": timestamp.isoformat(), "water_level_pct": 40.0,
            "source": "WOKWI_SIMULATED",
        })
        assert ingestion.live_zone_status["zone_01"]["source"] == "WOKWI_SIMULATED"
        assert ingestion.live_zone_status["zone_01"]["device_id"] == "node_adyar_01"

    def test_live_status_retains_telemetry_timestamp(self):
        storage = MemoryStorage()
        ingestion = IngestionService(storage=storage)
        timestamp = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
        record = ingestion.process_message({
            "device_id": "node_adyar_01", "zone_id": "zone_01", "sequence": 1,
            "timestamp": timestamp.isoformat(), "water_level_pct": 40.0,
            "source": "WOKWI_SIMULATED",
        })
        assert record is not None
        status = ingestion.zone_status["zone_01"]
        assert status["telemetry_timestamp"] == timestamp.isoformat()
        assert status["ingestion_timestamp"]

    def test_wokwi_dashboard_waits_without_live_data(self, monkeypatch):
        monkeypatch.setattr(config, "LIVE_TELEMETRY_SOURCE", "WOKWI_SIMULATED")
        state = {"zone_id": "zone_02", "current_level_cm": 12.0, "current_level_pct": 40.0}

        result = _apply_dashboard_status(state, {})

        assert result["live_data_state"] == "WAITING_FOR_WOKWI_DATA"
        assert result["water_level_pct"] is None
        assert result["sensor_status"] == "offline"

    def test_wokwi_dashboard_marks_old_measurement_stale(self, monkeypatch):
        monkeypatch.setattr(config, "LIVE_TELEMETRY_SOURCE", "WOKWI_SIMULATED")
        old_timestamp = (
            datetime.now(timezone.utc)
            - timedelta(seconds=config.STALE_READING_TIMEOUT_SEC + 1)
        ).isoformat()
        state = {"zone_id": "zone_03"}
        status = {
            "device_id": "node_kosasthalaiyar_01",
            "source": "WOKWI_SIMULATED",
            "water_level_cm": 10.0,
            "water_level_pct": 33.3,
            "telemetry_timestamp": old_timestamp,
            "last_update": old_timestamp,
        }

        result = _apply_dashboard_status(state, status)

        assert result["live_data_state"] == "STALE_WOKWI_DATA"
        assert result["sensor_status"] == "stale"
        assert result["is_stale"] is True


    def test_provenance_alias_normalization(self):
        """Verify legacy/lowercase source aliases normalize to canonical uppercase tags."""
        test_cases = [
            ("wokwi", DataSource.WOKWI_SIMULATED),
            ("WOKWI_SIMULATED", DataSource.WOKWI_SIMULATED),
            ("python_simulator", DataSource.PYTHON_SIMULATED),
            ("simulator", DataSource.PYTHON_SIMULATED),
            ("PYTHON_SIMULATED", DataSource.PYTHON_SIMULATED),
            ("observed", DataSource.OBSERVED),
            ("physical", DataSource.OBSERVED),
            ("OBSERVED", DataSource.OBSERVED),
            ("replayed_historical", DataSource.REPLAYED_HISTORICAL),
            ("REPLAYED_HISTORICAL", DataSource.REPLAYED_HISTORICAL),
            ("digital_twin", DataSource.DIGITAL_TWIN),
            ("forecast", DataSource.DIGITAL_TWIN),
            ("DIGITAL_TWIN", DataSource.DIGITAL_TWIN),
            ("reconstructed", DataSource.IMPUTED),
            ("imputed", DataSource.IMPUTED),
            ("IMPUTED", DataSource.IMPUTED),
        ]
        for raw_val, expected_enum in test_cases:
            msg = TelemetryMessage(
                device_id="dev_alias",
                zone_id="zone_01",
                sequence=1,
                timestamp=datetime.now(timezone.utc),
                source=raw_val,
            )
            assert msg.source == expected_enum

    def test_sensor_distance_to_water_level_conversion(self):
        """Verify water depth and percentage conversion formula with 30cm container height."""
        container_h = 30.0
        # distance = 10 cm -> water_depth = 30 - 10 = 20 cm -> pct = (20 / 30) * 100 = 66.67%
        dist = 10.0
        depth = container_h - dist
        pct = (depth / container_h) * 100.0

        msg = TelemetryMessage(
            device_id="sensor_node",
            zone_id="zone_01",
            sequence=1,
            timestamp=datetime.now(timezone.utc),
            distance_cm=dist,
            water_level_cm=round(depth, 2),
            water_level_pct=round(pct, 2),
            source=DataSource.WOKWI_SIMULATED,
        )
        assert msg.distance_cm == 10.0
        assert msg.water_level_cm == 20.0
        assert round(msg.water_level_pct, 1) == 66.7

    def test_water_level_percentage_clamping(self):
        """Verify water level percentage is clamped to [0.0, 100.0]."""
        msg_over = TelemetryMessage(
            device_id="d1",
            zone_id="zone_01",
            sequence=1,
            timestamp=datetime.now(timezone.utc),
            water_level_pct=140.0,
        )
        assert msg_over.water_level_pct == 100.0

        msg_under = TelemetryMessage(
            device_id="d2",
            zone_id="zone_01",
            sequence=2,
            timestamp=datetime.now(timezone.utc),
            water_level_pct=-15.0,
        )
        assert msg_under.water_level_pct == 0.0

    def test_invalid_sensor_reading_handled_safely(self):
        """Verify invalid readings are rejected safely without treating them as zero water level."""
        storage = MemoryStorage()
        ingestion = IngestionService(storage=storage)

        # Message with invalid sensor status and None depth (as emitted on sensor timeout)
        invalid_msg = {
            "device_id": "node_01",
            "zone_id": "zone_01",
            "sequence": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "distance_cm": None,
            "water_level_cm": None,
            "water_level_pct": None,
            "sensor_status": "invalid",
            "source": "WOKWI_SIMULATED",
        }
        record = ingestion.process_message(invalid_msg)
        assert record is not None
        assert record.sensor_status == SensorStatus.INVALID
        assert "invalid_reading" in record.quality_flags
        # Verify it did not trigger false flood threshold alert
        assert len(ingestion.alerts) == 0

    def test_prototype_thresholds_and_hysteresis(self):
        """Verify 70% warning and 85% critical prototype thresholds with 3% hysteresis."""
        storage = MemoryStorage()
        svc = IngestionService(storage=storage)
        base = datetime(2026, 10, 24, 10, 0, 0, tzinfo=timezone.utc)

        # Normal level: 50%
        svc.process_message({
            "device_id": "dev_th", "zone_id": "zone_01", "sequence": 1,
            "timestamp": (base + timedelta(seconds=5)).isoformat(),
            "water_level_pct": 50.0,
        })
        assert len(svc.alerts) == 0

        # Cross warning: 70.5% (>= 70%)
        svc.process_message({
            "device_id": "dev_th", "zone_id": "zone_01", "sequence": 2,
            "timestamp": (base + timedelta(seconds=10)).isoformat(),
            "water_level_pct": 70.5,
        })
        assert len(svc.alerts) == 1
        assert svc.alerts[-1].alert_level == AlertLevel.WARNING

        # Cross critical: 86.0% (>= 85%)
        svc.process_message({
            "device_id": "dev_th", "zone_id": "zone_01", "sequence": 3,
            "timestamp": (base + timedelta(seconds=15)).isoformat(),
            "water_level_pct": 86.0,
        })
        assert len(svc.alerts) == 2
        assert svc.alerts[-1].alert_level == AlertLevel.CRITICAL

        # Drops to 83.0% (inside hysteresis band: 85 - 3 = 82%) -> stays Critical
        svc.process_message({
            "device_id": "dev_th", "zone_id": "zone_01", "sequence": 4,
            "timestamp": (base + timedelta(seconds=20)).isoformat(),
            "water_level_pct": 83.0,
        })
        assert len(svc.alerts) == 2

        # Drops to 80.0% (< 82%) -> drops to Warning
        svc.process_message({
            "device_id": "dev_th", "zone_id": "zone_01", "sequence": 5,
            "timestamp": (base + timedelta(seconds=25)).isoformat(),
            "water_level_pct": 80.0,
        })
        assert len(svc.alerts) == 3
        assert svc.alerts[-1].alert_level == AlertLevel.WARNING

        # Drops below warning clearing band: 65.0% (< 67%) -> Normal
        svc.process_message({
            "device_id": "dev_th", "zone_id": "zone_01", "sequence": 6,
            "timestamp": (base + timedelta(seconds=30)).isoformat(),
            "water_level_pct": 65.0,
        })
        assert len(svc.alerts) == 4
        assert svc.alerts[-1].alert_level == AlertLevel.NORMAL


class TestMQTTCloudConfiguration:
    def test_tls_context_uses_verified_defaults(self, monkeypatch):
        monkeypatch.setattr(config, "MQTT_TLS_ENABLED", True)
        monkeypatch.setattr(config, "MQTT_TLS_CA_FILE", "")
        client = types.SimpleNamespace()
        captured = {}
        client.tls_set_context = lambda context: captured.setdefault("context", context)

        MQTTIngestionClient(IngestionService(MemoryStorage()))._configure_tls(client)

        context = captured["context"]
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True

    def test_cloud_client_uses_exact_configured_id_and_tls(self, monkeypatch):
        class FakeClient:
            instance = None

            def __init__(self, api_version, client_id):
                self.client_id = client_id
                self.tls_context = None
                self.username = None
                self.subscriptions = []
                self.on_connect = None
                self.on_disconnect = None
                self.on_message = None
                FakeClient.instance = self

            def username_pw_set(self, username, password):
                self.username = (username, password)

            def tls_set_context(self, context):
                self.tls_context = context

            def connect_async(self, host, port):
                self.endpoint = (host, port)

            def subscribe(self, topic, qos):
                self.subscriptions.append((topic, qos))

            def loop_start(self):
                pass

        mqtt_client_module = types.ModuleType("paho.mqtt.client")
        mqtt_client_module.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
        mqtt_client_module.Client = FakeClient
        mqtt_package = types.ModuleType("paho.mqtt")
        mqtt_package.client = mqtt_client_module
        paho_package = types.ModuleType("paho")
        paho_package.mqtt = mqtt_package
        monkeypatch.setitem(sys.modules, "paho", paho_package)
        monkeypatch.setitem(sys.modules, "paho.mqtt", mqtt_package)
        monkeypatch.setitem(sys.modules, "paho.mqtt.client", mqtt_client_module)

        monkeypatch.setattr(config, "MQTT_BROKER_HOST", "cloud.example")
        monkeypatch.setattr(config, "MQTT_BROKER_PORT", 8883)
        monkeypatch.setattr(config, "MQTT_USERNAME", "civictwin_backend")
        monkeypatch.setattr(config, "MQTT_PASSWORD", "test-only-placeholder")
        monkeypatch.setattr(config, "MQTT_CLIENT_ID", "civictwin_backend_01")
        monkeypatch.setattr(config, "MQTT_TLS_ENABLED", True)
        monkeypatch.setattr(config, "MQTT_TLS_CA_FILE", "")

        started = MQTTIngestionClient(IngestionService(MemoryStorage())).start()

        assert started is True
        assert FakeClient.instance.client_id == "civictwin_backend_01"
        assert FakeClient.instance.endpoint == ("cloud.example", 8883)
        assert FakeClient.instance.username == (
            "civictwin_backend",
            "test-only-placeholder",
        )
        assert FakeClient.instance.tls_context.verify_mode == ssl.CERT_REQUIRED
        assert FakeClient.instance.tls_context.check_hostname is True

        ingestion_client = MQTTIngestionClient(IngestionService(MemoryStorage()))
        ingestion_client._client = FakeClient.instance
        ingestion_client._on_connect(FakeClient.instance, None, None, 0)
        assert FakeClient.instance.subscriptions == [
            ("civictwin/v1/zones/+/telemetry", config.MQTT_QOS),
            ("civictwin/v1/zones/+/status", config.MQTT_QOS),
            ("civictwin/v1/zones/+/alerts", config.MQTT_QOS),
        ]
