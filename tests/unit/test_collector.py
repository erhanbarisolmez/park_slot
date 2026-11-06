"""DataCollector birim testleri."""

import json

import cv2
import numpy as np

from park_slot.training.collector import DataCollector


def _write_slot_config(path, slot_polygon):
    config = {
        "camera_id": "test",
        "image_width": 64,
        "image_height": 64,
        "slots": [{"id": 1, "label": "S1", "polygon": slot_polygon}],
    }
    path.write_text(json.dumps(config))


def test_save_crop_uses_captured_at_in_filename(tmp_path, monkeypatch):
    """captured_at verilirse dosya adı kaydetme anını değil o zamanı kullanmalı."""
    slot_config = tmp_path / "slots.json"
    _write_slot_config(slot_config, [[0, 0], [20, 0], [20, 20], [0, 20]])

    collector = DataCollector(slot_config, output_dir=tmp_path / "labeled")

    # GUI gerektirmesin diye imshow/waitKey'i sahteleştir: "e" tuşuna basılmış gibi davran
    monkeypatch.setattr(cv2, "imshow", lambda *a, **k: None)
    monkeypatch.setattr(cv2, "waitKey", lambda *a, **k: ord("e"))

    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    captured_at = 1_700_000_000.123

    should_continue = collector.collect_from_frame(frame, captured_at=captured_at)

    assert should_continue is True
    saved_files = list((tmp_path / "labeled" / "empty").glob("*.jpg"))
    assert len(saved_files) == 1
    assert saved_files[0].name == f"slot1_{int(captured_at * 1000)}.jpg"


def test_save_crop_falls_back_to_now_without_captured_at(tmp_path, monkeypatch):
    """captured_at verilmezse eski davranış (time.time()) korunmalı."""
    slot_config = tmp_path / "slots.json"
    _write_slot_config(slot_config, [[0, 0], [20, 0], [20, 20], [0, 20]])

    collector = DataCollector(slot_config, output_dir=tmp_path / "labeled")

    monkeypatch.setattr(cv2, "imshow", lambda *a, **k: None)
    monkeypatch.setattr(cv2, "waitKey", lambda *a, **k: ord("o"))

    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    collector.collect_from_frame(frame)

    saved_files = list((tmp_path / "labeled" / "occupied").glob("*.jpg"))
    assert len(saved_files) == 1
