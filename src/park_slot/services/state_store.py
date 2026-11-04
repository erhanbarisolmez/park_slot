"""Thread-safe durum deposu.

Neden thread-safe:
- Pipeline thread'i sürekli durumu günceller
- API thread'i aynı anda durumu okur
- WebSocket thread'i değişiklikleri broadcast eder
- Aynı anda okuma/yazma olursa veri bozulabilir
- threading.Lock ile bunu önlüyoruz
"""

import logging
import threading
import time
from typing import Optional

from park_slot.annotation.schema import ParkingStatus, SlotState, SlotStatus

logger = logging.getLogger(__name__)


class StateStore:
    """Tüm slotların anlık durumunu tutar."""

    def __init__(self, camera_id: str = "cam1"):
        self._camera_id = camera_id
        self._slots: dict[int, SlotState] = {}
        self._lock = threading.Lock()
        self._last_update: float = 0
        self._callbacks: list = []  # Durum değişince çağrılacak fonksiyonlar

    def update_slot(self, slot_id: int, label: str, status: SlotStatus, confidence: float) -> None:
        """Tek bir slot'un durumunu güncelle."""
        with self._lock:
            old = self._slots.get(slot_id)
            new_state = SlotState(
                id=slot_id,
                label=label,
                status=status,
                confidence=confidence,
            )
            self._slots[slot_id] = new_state
            self._last_update = time.time()

        # Durum değiştiyse callback'leri çağır
        if old is None or old.status != status:
            self._notify(new_state)

    def update_batch(self, updates: list[tuple[int, str, SlotStatus, float]]) -> None:
        """Birden fazla slot'u tek seferde güncelle.

        Args:
            updates: [(slot_id, label, status, confidence), ...]
        """
        changed = []
        with self._lock:
            for slot_id, label, status, confidence in updates:
                old = self._slots.get(slot_id)
                new_state = SlotState(
                    id=slot_id, label=label, status=status, confidence=confidence,
                )
                self._slots[slot_id] = new_state

                if old is None or old.status != status:
                    changed.append(new_state)

            self._last_update = time.time()

        for state in changed:
            self._notify(state)

    def get_status(self) -> ParkingStatus:
        """Tüm otoparkın anlık durumunu döndür."""
        with self._lock:
            slots = list(self._slots.values())

        occupied = sum(1 for s in slots if s.status == SlotStatus.OCCUPIED)
        empty = sum(1 for s in slots if s.status == SlotStatus.EMPTY)
        unknown = sum(1 for s in slots if s.status == SlotStatus.UNKNOWN)

        return ParkingStatus(
            camera_id=self._camera_id,
            total_slots=len(slots),
            occupied=occupied,
            empty=empty,
            unknown=unknown,
            slots=slots,
        )

    def get_slot(self, slot_id: int) -> Optional[SlotState]:
        """Tek bir slot'un durumunu döndür."""
        with self._lock:
            return self._slots.get(slot_id)

    @property
    def last_update(self) -> float:
        """Son güncelleme zamanı (timestamp)."""
        return self._last_update

    def on_change(self, callback) -> None:
        """Durum değişikliğinde çağrılacak fonksiyon kaydet.

        WebSocket broadcast için kullanılır.
        """
        self._callbacks.append(callback)

    def _notify(self, state: SlotState) -> None:
        """Callback'leri çağır."""
        for cb in self._callbacks:
            try:
                cb(state)
            except Exception as e:
                logger.error("State callback hatası: %s", e)
