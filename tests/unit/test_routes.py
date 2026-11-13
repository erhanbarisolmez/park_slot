"""REST API endpoint birim testleri (FastAPI TestClient).

Önceden sıfır coverage'a sahip modül - en kritik yol: endpoint'lerin
beklenen alanlarla/durum koduyla döndüğünü doğrulamak.
"""

from fastapi.testclient import TestClient

from park_slot.annotation.schema import SlotStatus
from park_slot.api.app import create_app
from park_slot.services.monitor import Monitor
from park_slot.services.prediction_log import PredictionLog
from park_slot.services.state_store import StateStore


def make_client(with_prediction_log: bool = False) -> TestClient:
    state_store = StateStore(camera_id="test")
    state_store.update_slot(1, "S1", SlotStatus.EMPTY, 0.9)
    monitor = Monitor()
    prediction_log = PredictionLog() if with_prediction_log else None
    app = create_app(state_store, monitor, prediction_log=prediction_log)
    return TestClient(app)


def test_health_returns_ok_without_pipeline():
    client = make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # Pipeline hiç set edilmediyse model_version/detected_vehicle_count None olmalı
    assert body["model_version"] is None
    assert body["detected_vehicle_count"] is None


def test_status_reflects_slot_state():
    client = make_client()
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["camera_id"] == "test"
    assert body["total_slots"] == 1
    assert body["empty"] == 1
    assert body["occupied"] == 0


def test_quality_503_without_prediction_log():
    client = make_client(with_prediction_log=False)
    resp = client.get("/api/v1/quality")
    assert resp.status_code == 503


def test_quality_ok_with_prediction_log():
    client = make_client(with_prediction_log=True)
    resp = client.get("/api/v1/quality")
    assert resp.status_code == 200
    body = resp.json()
    assert "flip_rate_per_hour" in body
    assert "unknown_rate" in body


def test_override_then_clear():
    client = make_client()

    resp = client.post("/api/v1/slots/1/override", json={"status": "occupied"})
    assert resp.status_code == 200
    assert resp.json()["manual_override"] is True
    assert resp.json()["status"] == "occupied"

    resp = client.delete("/api/v1/slots/1/override")
    assert resp.status_code == 200
    assert resp.json()["manual_override"] is False


def test_override_404_for_unknown_slot():
    client = make_client()
    resp = client.post("/api/v1/slots/999/override", json={"status": "empty"})
    assert resp.status_code == 404


def test_metrics_returns_prometheus_exposition_format():
    client = make_client(with_prediction_log=True)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "park_slot_model_info" in resp.text
    assert "park_slot_flip_rate_per_hour" in resp.text


def test_pending_overrides_503_without_pipeline():
    client = make_client()
    resp = client.get("/api/v1/pending-overrides")
    assert resp.status_code == 503


def test_pending_override_images_503_without_pipeline():
    client = make_client()
    assert client.get("/api/v1/pending-overrides/1/images").status_code == 503
    assert client.get("/api/v1/pending-overrides/1/images/occupied/slot1_1.jpg").status_code == 503
    assert client.delete("/api/v1/pending-overrides/1/images/occupied/slot1_1.jpg").status_code == 503
