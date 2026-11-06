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
    """Bir slotun anlık durumu.

    status: hysteresis'ten geçmiş, dışa raporlanan "onaylı" durum.
    raw_status: modelin o kareye ait ham tahmini (hysteresis uygulanmamış).
    stable_since: onaylı durumun en son değiştiği unix zamanı.
    minimap_x/minimap_y: varsa (perspective_transform ayarlıysa), slotun
        kuşbakışı düzlemdeki normalize (0-1) konumu - canlı video
        veremeyen istemciler (örn. mobil uygulama) bunu kendi basit harita
        UI'ında kullanabilir. perspective_transform yoksa None.
    """
    id: int
    label: str = ""
    status: SlotStatus = SlotStatus.UNKNOWN
    confidence: float = 0.0
    raw_status: SlotStatus = SlotStatus.UNKNOWN
    stable_since: float = 0.0
    minimap_x: Optional[float] = None
    minimap_y: Optional[float] = None


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
