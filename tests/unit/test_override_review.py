"""services/override_review.py birim testleri (pending override veri yönetimi)."""

import os
import time
from pathlib import Path

from park_slot.services.override_review import (
    approve,
    count_pending_by_slot,
    list_pending,
    list_pending_for_slot,
    pending_dir_for,
    reject,
    resolve_pending_image,
)


def make_pending_tree(tmp_path: Path, camera_id: str = "cam1") -> Path:
    pending = pending_dir_for(camera_id)
    pending = tmp_path / pending
    (pending / "empty").mkdir(parents=True)
    (pending / "occupied").mkdir(parents=True)
    return pending


def test_pending_dir_for_builds_expected_path():
    assert pending_dir_for("cam2") == Path("data/labeled_cam2/pending")


def test_count_pending_by_slot_aggregates_across_labels(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pending = make_pending_tree(tmp_path)
    (pending / "empty" / "slot1_1.jpg").write_bytes(b"x")
    (pending / "occupied" / "slot1_2.jpg").write_bytes(b"x")
    (pending / "occupied" / "slot2_3.jpg").write_bytes(b"x")

    counts = count_pending_by_slot("cam1")

    assert counts == {1: 2, 2: 1}


def test_count_pending_by_slot_empty_when_no_pending_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert count_pending_by_slot("cam1") == {}


def test_approve_and_reject_still_work_via_service_module(tmp_path):
    pending = make_pending_tree(tmp_path)
    keep = pending / "occupied" / "slot5_1.jpg"
    keep.write_bytes(b"x")
    drop = pending / "empty" / "slot6_2.jpg"
    drop.write_bytes(b"x")

    approve(keep)
    reject(drop)

    assert not keep.exists()
    assert not drop.exists()
    assert (tmp_path / "data" / "labeled_cam1" / "occupied" / "slot5_1.jpg").exists()


def test_list_pending_sorted_oldest_first(tmp_path):
    pending = make_pending_tree(tmp_path)
    older = pending / "empty" / "slot1_1.jpg"
    newer = pending / "empty" / "slot1_2.jpg"
    older.write_bytes(b"x")
    newer.write_bytes(b"x")
    # Dosya sistemi mtime çözünürlüğü düşük olabilir - sıralamayı garantiye al.
    now = time.time()
    os.utime(older, (now, now))
    os.utime(newer, (now + 10, now + 10))

    result = list_pending(pending)

    assert [p.name for p in result] == ["slot1_1.jpg", "slot1_2.jpg"]


def test_list_pending_for_slot_filters_by_slot_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pending = make_pending_tree(tmp_path)
    (pending / "empty" / "slot1_1.jpg").write_bytes(b"x")
    (pending / "occupied" / "slot2_2.jpg").write_bytes(b"x")
    (pending / "occupied" / "slot1_3.jpg").write_bytes(b"x")

    result = list_pending_for_slot("cam1", 1)

    assert {(label, p.name) for label, p in result} == {
        ("empty", "slot1_1.jpg"),
        ("occupied", "slot1_3.jpg"),
    }


def test_resolve_pending_image_valid_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pending = make_pending_tree(tmp_path)
    target = pending / "occupied" / "slot1_12345.jpg"
    target.write_bytes(b"x")

    resolved = resolve_pending_image("cam1", 1, "occupied", "slot1_12345.jpg")

    assert resolved.resolve() == target.resolve()


def test_resolve_pending_image_rejects_wrong_slot_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pending = make_pending_tree(tmp_path)
    (pending / "occupied" / "slot1_12345.jpg").write_bytes(b"x")

    # Dosya slot1'e ait ama slot2 olarak istendi - eşleşmemeli.
    assert resolve_pending_image("cam1", 2, "occupied", "slot1_12345.jpg") is None


def test_resolve_pending_image_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_pending_tree(tmp_path)

    assert resolve_pending_image("cam1", 1, "occupied", "../../../etc/passwd") is None
    assert resolve_pending_image("cam1", 1, "occupied", "slot1_1.jpg/../../x") is None
    assert resolve_pending_image("cam1", 1, "../other_cam", "slot1_1.jpg") is None


def test_resolve_pending_image_rejects_missing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_pending_tree(tmp_path)

    assert resolve_pending_image("cam1", 1, "occupied", "slot1_99999.jpg") is None
