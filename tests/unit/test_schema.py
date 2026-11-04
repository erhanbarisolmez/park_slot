"""Annotation schema birim testleri."""

import pytest
from pydantic import ValidationError

from park_slot.annotation.schema import (
    CameraSlotConfig,
    ParkingStatus,
    SlotDefinition,
    SlotState,
    SlotStatus,
)


def test_slot_definition_valid():
    """Geçerli slot tanımı."""
    slot = SlotDefinition(id=1, label="A1", polygon=[[0, 0], [100, 0], [100, 100]])
    assert slot.id == 1
    assert len(slot.polygon) == 3


def test_slot_definition_min_points():
    """Polygon en az 3 nokta olmalı."""
    with pytest.raises(ValidationError):
        SlotDefinition(id=1, polygon=[[0, 0], [1, 1]])


def test_slot_status_enum():
    """SlotStatus enum değerleri."""
    assert SlotStatus.EMPTY == "empty"
    assert SlotStatus.OCCUPIED == "occupied"
    assert SlotStatus.UNKNOWN == "unknown"


def test_parking_status_counts():
    """ParkingStatus doluluk sayıları."""
    status = ParkingStatus(
        camera_id="cam1",
        total_slots=3,
        occupied=1,
        empty=2,
        slots=[
            SlotState(id=1, status=SlotStatus.EMPTY, confidence=0.9),
            SlotState(id=2, status=SlotStatus.OCCUPIED, confidence=0.85),
            SlotState(id=3, status=SlotStatus.EMPTY, confidence=0.95),
        ],
    )
    assert status.occupied == 1
    assert status.empty == 2


def test_camera_slot_config():
    """CameraSlotConfig oluşturma."""
    config = CameraSlotConfig(
        camera_id="test",
        slots=[
            SlotDefinition(id=1, label="S1", polygon=[[0, 0], [10, 0], [10, 10], [0, 10]]),
        ],
    )
    assert len(config.slots) == 1
    assert config.camera_id == "test"
