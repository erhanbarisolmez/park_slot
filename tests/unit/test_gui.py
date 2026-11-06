"""annotation.gui (SlotAnnotator) birim testleri."""

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
