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
import numpy as np

from park_slot.annotation.schema import SlotStatus
from park_slot.capture.base import FrameSource
from park_slot.inference.engine import InferenceEngine
from park_slot.inference.overlay import draw_overlay
from park_slot.inference.vehicle_detector import VehicleDetector, assign_to_slots
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
        vehicle_detector: VehicleDetector | None = None,
        vehicle_detector_interval: int = 1,
    ):
        self._source = source
        self._engine = engine
        self._slot_manager = slot_manager
        self._state_store = state_store
        self._monitor = monitor
        self._frame_interval = frame_interval
        self._show_window = show_window
        self._vehicle_detector = vehicle_detector
        # Dedektör (~450ms/kare, bkz. vehicle_detector.py) sadece yavaş
        # değişen bir fiziksel durumu (park edilmiş araç) geometrik olarak
        # düzeltiyor - gerçek zamanlı takip değil, her kare çalıştırmaya
        # gerek yok. Atlanan karelerde son sonuç cache'den kullanılır.
        self._vehicle_detector_interval = max(1, vehicle_detector_interval)
        self._frame_count = 0
        self._cached_vehicle_detections: list = []
        self._cached_assigned_ids: set[int] = set()
        self._running = False
        self._thread: threading.Thread | None = None
        self._display_lock = threading.Lock()
        self._latest_display = None
        self._latest_vehicle_count: int | None = None
        # /live/frame.jpg her poll'da yeniden encode etmesin diye - sadece
        # burada yeni bir display frame üretildiğinde artar.
        self._display_version = 0

        # Varsa kuşbakışı mini-harita için homografi matrisi (bkz. tools/mark_perspective.py)
        self._perspective_transform = slot_manager.config.perspective_transform

        # Slot polygon'larını sözlüğe çevir (overlay için)
        self._polygons = {
            slot.id: slot.polygon for slot in slot_manager.slots
        }

        # Araç dedektörü için ilgi alanı (ROI) - tüm slot polygon'larının
        # kapsadığı bölge + %15 pay. Bazı kameraların kendi overlay'inde
        # (hava durumu widget'ı, tarihi fotoğraf, küçük ek görüntü gibi)
        # köşelerde gömülü resimler oluyor; dedektör tüm karede çalıştığı
        # için bu resimlerin İÇİNDEKİ araçları da "tespit" edip hem
        # get_vehicle_count()'u şişiriyor hem de /live'da alakasız mavi
        # kutular çiziyordu (slot durumunu etkilemiyordu çünkü bu bölgeler
        # hiçbir slot polygonuyla kesişmiyor, ama kafa karıştırıcıydı).
        self._detection_roi = self._compute_detection_roi(slot_manager)

    @staticmethod
    def _compute_detection_roi(slot_manager: SlotManager) -> tuple[float, float, float, float]:
        all_points = [p for slot in slot_manager.slots for p in slot.polygon]
        xs = [p[0] for p in all_points]
        ys = [p[1] for p in all_points]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        pad_x, pad_y = (x1 - x0) * 0.15, (y1 - y0) * 0.15
        img_w = slot_manager.config.image_width
        img_h = slot_manager.config.image_height
        return (
            max(0, x0 - pad_x), max(0, y0 - pad_y),
            min(img_w, x1 + pad_x), min(img_h, y1 + pad_y),
        )

    def find_slot_at(self, x: int, y: int) -> int | None:
        """(x, y) orijinal kare piksel koordinatını içeren slot'un id'sini döndür.

        /live sayfasındaki tıklama-ile-seç özelliği için - operatör
        görüntüdeki bir slota tıklayınca hangi slotu kastettiğini bulur.
        """
        point = (float(x), float(y))
        for slot_id, polygon in self._polygons.items():
            pts = np.array(polygon, dtype=np.int32)
            if cv2.pointPolygonTest(pts, point, False) >= 0:
                return slot_id
        return None

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

    def get_display_frame(self):
        """Son render edilen kareyi döndür (ana thread'den cv2.imshow için)."""
        with self._display_lock:
            return self._latest_display

    def get_display_version(self) -> int:
        """Son display frame'in versiyonu (değişmediyse çağıran taraf cache'ini kullanabilir)."""
        with self._display_lock:
            return self._display_version

    def get_vehicle_count(self) -> int | None:
        """Dedektörün slot'tan BAĞIMSIZ, ham araç sayısı (detector kapalıysa None).

        Slot bazlı doluluk sayısıyla karşılaştırmak için: bir araç eğri/kötü
        park edilip birden fazla slot'a görsel taşırsa, "dolu slot sayısı"
        ile "gerçek araç sayısı" birbirinden ayrılır - bu metod ikincisini verir.
        """
        with self._display_lock:
            return self._latest_vehicle_count

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

        # 4. YOLO ile hibrit düzeltme (opsiyonel) - açılı kamerada bir aracın
        # gövdesi birden fazla komşu slot'a görsel taştığında CNN'in hepsini
        # "occupied" sanmasını önler (bkz. inference/vehicle_detector.py).
        #
        # NOT: burada sadece POZİTİF düzeltme uygulanır (zemin noktası
        # düşen slot -> OCCUPIED); CNN'in "occupied" kararını EMPTY'ye
        # çeviren bir "suppression" dalı denendi ama kamera açısına göre
        # zemin noktası hesaplaması (ground_offset sabiti) her 3 kamerada
        # güvenilir şekilde doğru slota düşmediği için cam1 S2/S3 ve cam3
        # S3/S5/S6'da gerçekten dolu slotları yanlışlıkla boşalttı -
        # kaldırıldı. Yanlış "boş" (gerçekte dolu), yanlış "dolu"dan daha
        # zararlı (kullanıcıyı boşuna oraya yönlendirir).
        self._frame_count += 1
        assigned_ids: set[int] = set()
        vehicle_detections: list = []
        if self._vehicle_detector is not None:
            run_detector = self._frame_count % self._vehicle_detector_interval == 0
            if run_detector:
                try:
                    raw_detections = self._vehicle_detector.detect(frame)
                    rx0, ry0, rx1, ry1 = self._detection_roi
                    vehicle_detections = [
                        d for d in raw_detections
                        if rx0 <= d.ground_point[0] <= rx1 and ry0 <= d.ground_point[1] <= ry1
                    ]
                    assigned_ids = assign_to_slots(vehicle_detections, self._polygons)
                    self._cached_vehicle_detections = vehicle_detections
                    self._cached_assigned_ids = assigned_ids
                except Exception as e:
                    logger.error("Araç tespiti hatası: %s", e)
            else:
                vehicle_detections = self._cached_vehicle_detections
                assigned_ids = self._cached_assigned_ids

        # 5. Durumu güncelle
        updates = []
        for slot_def, (status, confidence) in zip(slots, results):
            if slot_def.id in assigned_ids:
                # Dedektör bir aracı zemin noktasıyla bu slota atadı -
                # geometrik sinyal CNN'den daha güvenilir, occupied kabul edilir.
                status, confidence = SlotStatus.OCCUPIED, max(confidence, 0.9)

            updates.append((slot_def.id, slot_def.label, status, confidence))

        self._state_store.update_batch(updates)

        # 6. Görselleştir - imshow/waitKey Qt GUI thread güvenliği gerektirdiği
        # için burada çağrılmaz; kare saklanır (ana thread show_window ile
        # gösterir, API /live/* endpoint'leri de buradan okur).
        parking_status = self._state_store.get_status()
        display = draw_overlay(
            frame, parking_status, self._polygons,
            perspective_transform=self._perspective_transform,
            vehicle_count=len(vehicle_detections) if self._vehicle_detector is not None else None,
        )

        # Dedektörün ham tespitlerini mavi kutuyla çiz - ama SADECE hiçbir
        # slota eşleşmeyenleri. Bir araç zaten bir slota atanıp kırmızı
        # (dolu) gösteriliyorsa üstüne ayrıca mavi kutu çizmek gereksiz/kafa
        # karıştırıcı; asıl dikkat çekilmesi gereken, hiçbir slot polygonuna
        # düşmeyen araçlar (çift park, sokakta duran, slot tanımı eksik vb.).
        # Toplam sayı (bilgi panelindeki "Dedektör: N araç") yine TÜM
        # tespitleri sayar - sadece çizim filtreleniyor.
        for det in vehicle_detections:
            in_any_slot = any(
                cv2.pointPolygonTest(np.array(poly, dtype=np.int32), det.ground_point, False) >= 0
                for poly in self._polygons.values()
            )
            if in_any_slot:
                continue
            x1, y1, x2, y2 = (int(v) for v in det.bbox)
            cv2.rectangle(display, (x1, y1), (x2, y2), (255, 150, 0), 2)
            gx, gy = (int(v) for v in det.ground_point)
            cv2.circle(display, (gx, gy), 5, (255, 150, 0), -1)

        with self._display_lock:
            self._latest_display = display
            self._latest_vehicle_count = (
                len(vehicle_detections) if self._vehicle_detector is not None else None
            )
            self._display_version += 1

        logger.debug(
            "Frame işlendi: %d slot, infer=%.1fms",
            len(results),
            self._monitor.avg_inference_time,
        )
