"""Slot tanımlama veri modelleri."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SlotStatus(str, Enum):
    """Park yeri durumu."""
    EMPTY = "empty"
    OCCUPIED = "occupied"
    UNKNOWN = "unknown"


class SlotDefinition(BaseModel):
    """Tek bir park slotunun tanımı.

    polygon: Slot köşelerinin piksel koordinatları [[x1,y1], [x2,y2], ...]
    label: Slot adı (örn: "A1", "B3")
    """
    id: int
    label: str = ""
    polygon: list[list[int]] = Field(..., min_length=3)


class SlotState(BaseModel):
    """Bir slotun anlık durumu."""
    id: int
    label: str = ""
    status: SlotStatus = SlotStatus.UNKNOWN
    confidence: float = 0.0


class CameraSlotConfig(BaseModel):
    """Bir kameraya ait tüm slotların konfigürasyonu."""
    camera_id: str
    source_url: str = ""
    image_width: int = 1280
    image_height: int = 720
    slots: list[SlotDefinition] = []
    perspective_transform: Optional[list[list[float]]] = None


class ParkingStatus(BaseModel):
    """Tüm otoparkın anlık durumu."""
    camera_id: str
    total_slots: int = 0
    occupied: int = 0
    empty: int = 0
    unknown: int = 0
    slots: list[SlotState] = []
