"""Pipeline.save_override_label birim testi (override → eğitim verisi feedback loop)."""

from pathlib import Path

import numpy as np
import pytest

from park_slot.annotation.schema import SlotStatus
from park_slot.inference.pipeline import Pipeline
from park_slot.services.monitor import Monitor
from park_slot.services.slot_manager import SlotManager
from park_slot.services.state_store import StateStore

REPO_ROOT = Path(__file__).resolve().parents[2]
SLOTS_PATH = REPO_ROOT / "config" / "slots" / "cam1.json"

pytestmark = pytest.mark.skipif(
    not SLOTS_PATH.exists(), reason="config/slots/cam1.json bulunamadı"
)


class _NoopSource:
    def open(self):
        pass

    def read(self):
        return None

    def is_open(self):
        return True

    def close(self):
        pass


def make_pipeline() -> Pipeline:
    slot_manager = SlotManager(SLOTS_PATH, input_size=64)
    return Pipeline(
        source=_NoopSource(),
        engine=None,  # save_override_label hiç kullanmıyor
        slot_manager=slot_manager,
        state_store=StateStore(camera_id=slot_manager.camera_id),
        monitor=Monitor(),
        frame_interval=1.5,
    )


def test_save_override_label_writes_training_image(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # repo'nun gerçek data/ klasörünü kirletme

    pipeline = make_pipeline()
    slot_id = pipeline._slot_manager.slots[0].id
    fake_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    pipeline._latest_raw_frame = fake_frame

    ok = pipeline.save_override_label(slot_id, SlotStatus.OCCUPIED)

    assert ok is True
    # Doğrudan eğitim setine değil, onay bekleyen "pending" klasörüne yazılır
    # (bkz. tools/review_overrides.py) - veri güvenliği için.
    saved = list(
        (tmp_path / f"data/labeled_{pipeline._slot_manager.camera_id}" / "pending" / "occupied").glob("*.jpg")
    )
    assert len(saved) == 1


def test_save_override_label_skips_unknown_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    pipeline = make_pipeline()
    slot_id = pipeline._slot_manager.slots[0].id
    pipeline._latest_raw_frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    ok = pipeline.save_override_label(slot_id, SlotStatus.UNKNOWN)

    assert ok is False
    assert not (tmp_path / "data").exists()


def test_save_override_label_no_frame_yet(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    pipeline = make_pipeline()
    slot_id = pipeline._slot_manager.slots[0].id

    ok = pipeline.save_override_label(slot_id, SlotStatus.EMPTY)

    assert ok is False


def test_get_pending_override_counts_reflects_saved_labels(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    pipeline = make_pipeline()
    slot_id = pipeline._slot_manager.slots[0].id
    pipeline._latest_raw_frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    pipeline.save_override_label(slot_id, SlotStatus.OCCUPIED)
    pipeline.save_override_label(slot_id, SlotStatus.EMPTY)

    counts = pipeline.get_pending_override_counts()

    assert counts == {slot_id: 2}


def test_list_and_delete_pending_override_image(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    pipeline = make_pipeline()
    slot_id = pipeline._slot_manager.slots[0].id
    pipeline._latest_raw_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    pipeline.save_override_label(slot_id, SlotStatus.OCCUPIED)

    images = pipeline.list_pending_overrides_for_slot(slot_id)
    assert len(images) == 1
    label, filename = images[0]
    assert label == "occupied"

    resolved = pipeline.resolve_pending_override_image(slot_id, label, filename)
    assert resolved is not None and resolved.exists()

    deleted = pipeline.delete_pending_override_image(slot_id, label, filename)
    assert deleted is True
    assert pipeline.list_pending_overrides_for_slot(slot_id) == []

    # İkinci silme artık dosya yok, False dönmeli (hata fırlatmamalı).
    assert pipeline.delete_pending_override_image(slot_id, label, filename) is False
