"""Ana çıkarım pipeline - tüm parçaları birleştirir.

Akış:
  capture (kare al)
  → slot_manager (kırp + tensor)
  → engine (batch tahmin)
  → state_store (durumu güncelle)
  → overlay (görselleştir)

Bu pipeline ayrı bir thread'de çalışır.
API ve WebSocket ana thread'den durumu okur.
"""

import logging
import threading
import time

import cv2

from park_slot.capture.base import FrameSource
from park_slot.inference.engine import InferenceEngine
from park_slot.inference.overlay import draw_overlay
from park_slot.services.monitor import Monitor
from park_slot.services.slot_manager import SlotManager
from park_slot.services.state_store import StateStore

logger = logging.getLogger(__name__)


class Pipeline:
    """Gerçek zamanlı çıkarım pipeline."""

    def __init__(
        self,
        source: FrameSource,
        engine: InferenceEngine,
        slot_manager: SlotManager,
        state_store: StateStore,
        monitor: Monitor,
        frame_interval: float = 2.0,
        show_window: bool = False,
    ):
        self._source = source
        self._engine = engine
        self._slot_manager = slot_manager
        self._state_store = state_store
        self._monitor = monitor
        self._frame_interval = frame_interval
        self._show_window = show_window
        self._running = False
        self._thread: threading.Thread | None = None

        # Slot polygon'larını sözlüğe çevir (overlay için)
        self._polygons = {
            slot.id: slot.polygon for slot in slot_manager.slots
        }

    def start(self) -> None:
        """Pipeline'ı arka plan thread'inde başlat."""
        if self._running:
            logger.warning("Pipeline zaten çalışıyor")
            return

        self._running = True
        self._source.open()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Pipeline başlatıldı (interval=%.1fs)", self._frame_interval)

    def stop(self) -> None:
        """Pipeline'ı durdur."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=10)
        self._source.close()

        if self._show_window:
            cv2.destroyAllWindows()

        logger.info("Pipeline durduruldu")

    @property
    def is_running(self) -> bool:
        return self._running

    def _loop(self) -> None:
        """Ana döngü - thread'de çalışır."""
        while self._running:
            frame_start = time.time()

            try:
                self._process_frame()
            except Exception as e:
                logger.error("Frame işleme hatası: %s", e)

            # Frame interval kadar bekle
            elapsed = time.time() - frame_start
            self._monitor.record_frame(elapsed)

            sleep_time = max(0, self._frame_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _process_frame(self) -> None:
        """Tek bir kareyi işle: yakala → kırp → tahmin → güncelle."""
        # 1. Kare yakala
        frame = self._source.read()
        if frame is None:
            logger.warning("Kare alınamadı")
            return

        # 2. Slotları kırp ve tensor'a çevir
        slots, tensors = self._slot_manager.get_tensors(frame)
        if not tensors:
            return

        # 3. Batch tahmin
        infer_start = time.time()
        results = self._engine.predict_batch(tensors)
        self._monitor.record_inference(time.time() - infer_start)

        # 4. Durumu güncelle
        updates = []
        for slot_def, (status, confidence) in zip(slots, results):
            updates.append((slot_def.id, slot_def.label, status, confidence))

        self._state_store.update_batch(updates)

        # 5. Görselleştir (opsiyonel)
        if self._show_window:
            parking_status = self._state_store.get_status()
            display = draw_overlay(frame, parking_status, self._polygons)
            cv2.imshow("ParkSlot Live", display)
            cv2.waitKey(1)

        logger.debug(
            "Frame işlendi: %d slot, infer=%.1fms",
            len(results),
            self._monitor.avg_inference_time,
        )
