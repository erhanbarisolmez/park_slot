"""annotation.gui (SlotAnnotator) birim testleri."""

import json

import numpy as np

from park_slot.annotation.gui import SlotAnnotator
from park_slot.annotation.schema import SlotDefinition

_IMAGE = np.zeros((100, 100, 3), dtype=np.uint8)


def test_init_with_initial_slots_prepopulates_and_continues_ids():
    initial = [
        SlotDefinition(id=5, label="S5", polygon=[[0, 0], [1, 0], [1, 1]]),
        SlotDefinition(id=7, label="S7", polygon=[[2, 2], [3, 2], [3, 3]]),
    ]
    annotator = SlotAnnotator(_IMAGE, initial_slots=initial)

    assert annotator._slots == initial
    assert annotator._next_id == 8


def test_init_without_initial_slots_preserves_old_behavior():
    annotator = SlotAnnotator(_IMAGE)

    assert annotator._slots == []
    assert annotator._next_id == 1


def test_init_with_empty_initial_slots_list_same_as_none():
    annotator = SlotAnnotator(_IMAGE, initial_slots=[])

    assert annotator._slots == []
    assert annotator._next_id == 1


def test_save_to_file_starts_at_version_1(tmp_path):
    """GAP #10: hiç dosya yoksa version 1'den başlar, updated_at dolar."""
    annotator = SlotAnnotator(_IMAGE)
    path = tmp_path / "cam1.json"

    annotator.save_to_file(path)

    data = json.loads(path.read_text())
    assert data["version"] == 1
    assert data["updated_at"] is not None


def test_save_to_file_increments_version_on_each_save(tmp_path):
    """GAP #10: mapping değişikliklerinin izlenebilirliği - ardışık kayıtlarda
    version artmalı, perspective_transform korunmalı."""
    path = tmp_path / "cam1.json"
    first = SlotAnnotator(_IMAGE)
    first.save_to_file(path)

    data = json.loads(path.read_text())
    data["perspective_transform"] = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    path.write_text(json.dumps(data))

    second = SlotAnnotator(_IMAGE)
    second.save_to_file(path)

    data = json.loads(path.read_text())
    assert data["version"] == 2
    assert data["perspective_transform"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
