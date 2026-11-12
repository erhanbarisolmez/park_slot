"""OpenCV tabanlı polygon çizim GUI.

Bu araçla bir kare üzerinde park slotlarını fare ile işaretlersin.
Her slot en az 3 noktalı bir polygon.

Kontroller:
- Sol tık: Nokta ekle
- Sağ tık: Son noktayı geri al
- Enter: Mevcut polygon'u kaydet, yeni slot'a geç
- 'u': Son slot'u geri al (undo)
- 's': Tüm slotları JSON olarak kaydet ve pencereyi kapat
- 'q' veya ESC: Kaydetmeden çık
"""

import json
import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from park_slot.annotation.geometry import order_points_by_angle
from park_slot.annotation.schema import CameraSlotConfig, SlotDefinition

logger = logging.getLogger(__name__)

# Çizim renkleri (BGR)
COLOR_ACTIVE = (0, 255, 255)    # Sarı - çizim sırasındaki polygon
COLOR_SAVED = (0, 255, 0)       # Yeşil - kaydedilmiş slotlar
COLOR_POINT = (0, 0, 255)       # Kırmızı - köşe noktaları
COLOR_TEXT = (255, 255, 255)     # Beyaz - etiketler


class SlotAnnotator:
    """Park slotlarını fare ile işaretleme aracı."""

    def __init__(
        self,
        image: np.ndarray,
        camera_id: str = "cam1",
        source_url: str = "",
        initial_slots: Optional[list[SlotDefinition]] = None,
    ):
        """
        Args:
            initial_slots: Önceden yüklenmiş slotlar (örn. tools/annotate_birdseye.py'nin
                mevcut slotları kuşbakışı uzaya projekte edip buraya aktarması için) -
                verilirse kaydedilmiş slot olarak gösterilir, id numaralandırması
                en büyük mevcut id'den devam eder. Verilmezse (None, varsayılan)
                eski davranış birebir korunur (boş liste, id 1'den başlar).
        """
        self._image = image.copy()
        self._camera_id = camera_id
        self._source_url = source_url
        self._current_points: list[list[int]] = []
        self._slots: list[SlotDefinition] = list(initial_slots) if initial_slots else []
        self._next_id = max((s.id for s in self._slots), default=0) + 1
        self._window_name = "Park Slot Annotator"

    def _mouse_callback(self, event: int, x: int, y: int, flags: int, param) -> None:
        """Fare olaylarını yakala."""
        if event == cv2.EVENT_LBUTTONDOWN:
            # Sol tık: nokta ekle
            self._current_points.append([x, y])
            logger.debug("Nokta eklendi: (%d, %d)", x, y)

        elif event == cv2.EVENT_RBUTTONDOWN:
            if self._current_points:
                # Aktif çizim varken sağ tık: son noktayı sil
                removed = self._current_points.pop()
                logger.debug("Nokta silindi: (%d, %d)", removed[0], removed[1])
            else:
                # Çizim yokken sağ tık: tıklanan noktadaki kaydedilmiş slotu sil
                # (initial_slots ile yüklenmiş mevcut slotları düzenlemek için)
                self._delete_slot_at(x, y)

    def _delete_slot_at(self, x: int, y: int) -> None:
        """(x, y) noktasını içeren kaydedilmiş slotu sil (varsa, en üsttekini)."""
        for i in range(len(self._slots) - 1, -1, -1):
            pts = np.array(self._slots[i].polygon, dtype=np.int32)
            if cv2.pointPolygonTest(pts, (float(x), float(y)), False) >= 0:
                removed = self._slots.pop(i)
                logger.info("Slot silindi: %s", removed.label)
                return

    def _draw(self) -> np.ndarray:
        """Görüntü üzerine slotları ve aktif polygon'u çiz."""
        canvas = self._image.copy()

        # Kaydedilmiş slotları çiz
        for slot in self._slots:
            pts = np.array(slot.polygon, dtype=np.int32)
            cv2.polylines(canvas, [pts], True, COLOR_SAVED, 2)
            # Slot etiketini merkeze yaz
            cx = int(np.mean([p[0] for p in slot.polygon]))
            cy = int(np.mean([p[1] for p in slot.polygon]))
            label = slot.label or f"S{slot.id}"
            cv2.putText(canvas, label, (cx - 10, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_TEXT, 1)

        # Aktif polygon'u çiz
        if self._current_points:
            pts = np.array(self._current_points, dtype=np.int32)
            if len(self._current_points) > 1:
                cv2.polylines(canvas, [pts], False, COLOR_ACTIVE, 2)
            for pt in self._current_points:
                cv2.circle(canvas, (pt[0], pt[1]), 4, COLOR_POINT, -1)

        # Bilgi çubuğu
        info = f"Slot #{self._next_id} | Noktalar: {len(self._current_points)} | Toplam: {len(self._slots)}"
        cv2.putText(canvas, info, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_TEXT, 2)
        cv2.putText(canvas, "Sol:Ekle | Sag:Geri/Slot Sil | Enter:Kaydet | S:Dosyala ve Cik | Q:Kaydetmeden Cik",
                    (10, canvas.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_TEXT, 1)

        return canvas

    def _save_current_slot(self) -> None:
        """Aktif polygon'u slot olarak kaydet."""
        if len(self._current_points) < 3:
            logger.warning("En az 3 nokta gerekli (mevcut: %d)", len(self._current_points))
            return

        # Tıklama sırası karışık olsa da basit/convex bir polygon elde etmek için
        # köşeleri centroid etrafındaki açıya göre yeniden sırala (bkz. slot 3 bug'ı)
        ordered_points = order_points_by_angle(self._current_points)

        slot = SlotDefinition(
            id=self._next_id,
            label=f"S{self._next_id}",
            polygon=ordered_points,
        )
        self._slots.append(slot)
        logger.info("Slot kaydedildi: %s (%d nokta)", slot.label, len(slot.polygon))

        self._current_points = []
        self._next_id += 1

    def _undo_last_slot(self) -> None:
        """Son kaydedilen slot'u geri al."""
        if self._slots:
            removed = self._slots.pop()
            self._next_id -= 1
            logger.info("Slot geri alındı: %s", removed.label)

    def save_to_file(self, path: str | Path) -> None:
        """Slotları JSON dosyasına kaydet."""
        path = Path(path)

        # Dosyada zaten perspective_transform varsa (tools/mark_perspective.py
        # ile ayarlanmış kuşbakışı referans noktaları) koru - slotları yeniden
        # çizmek bu kalibrasyonu silmemeli, aksi halde minimap ve toplu
        # engellenme tespitinin fiziksel yakınlık filtresi bozulur.
        existing_perspective_transform = None
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing_perspective_transform = json.load(f).get("perspective_transform")
            except (json.JSONDecodeError, OSError):
                pass

        h, w = self._image.shape[:2]
        config = CameraSlotConfig(
            camera_id=self._camera_id,
            source_url=self._source_url,
            image_width=w,
            image_height=h,
            slots=self._slots,
            perspective_transform=existing_perspective_transform,
        )

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(config.model_dump_json(indent=2))

        logger.info("Slotlar kaydedildi: %s (%d slot)", path, len(self._slots))

    def run(self, save_path: Optional[str | Path] = None) -> list[SlotDefinition]:
        """GUI'yi başlat. Kapatılınca slot listesini döndür."""
        cv2.namedWindow(self._window_name)
        cv2.setMouseCallback(self._window_name, self._mouse_callback)

        logger.info("Annotator başlatıldı. Slotları işaretleyin.")

        while True:
            canvas = self._draw()
            cv2.imshow(self._window_name, canvas)

            key = cv2.waitKey(30) & 0xFF

            if key == 13:  # Enter
                self._save_current_slot()

            elif key == ord("u"):
                self._undo_last_slot()

            elif key == ord("s"):
                path = save_path or f"config/slots/{self._camera_id}.json"
                self.save_to_file(path)
                break

            elif key == ord("q") or key == 27:  # Q veya ESC
                break

        cv2.destroyWindow(self._window_name)
        return self._slots
