"""Veri toplama aracı.

Kameradan kare alır, tanımlı slotları kırpar ve kullanıcıya sorar:
- 'e': Empty (boş)
- 'o': Occupied (dolu)
- 's': Skip (atla)
- 'q': Çık

Etiketlenen görüntüler data/labeled/{empty,occupied}/ altına kaydedilir.
"""

import json
import logging
import time
from pathlib import Path

import cv2
import numpy as np

from park_slot.annotation.schema import CameraSlotConfig
from park_slot.utils.image import crop_polygon, resize

logger = logging.getLogger(__name__)


class DataCollector:
    """Slot kırpma ve etiketleme aracı."""

    def __init__(
        self,
        slot_config_path: str | Path,
        output_dir: str | Path = "data/labeled",
        crop_size: int = 64,
    ):
        self._config = self._load_config(slot_config_path)
        self._output_dir = Path(output_dir)
        self._crop_size = crop_size
        self._counts = {"empty": 0, "occupied": 0, "skipped": 0}

        # Çıktı klasörlerini oluştur
        (self._output_dir / "empty").mkdir(parents=True, exist_ok=True)
        (self._output_dir / "occupied").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _load_config(path: str | Path) -> CameraSlotConfig:
        """Slot konfigürasyonunu JSON'dan yükle."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return CameraSlotConfig(**data)

    def _save_crop(self, image: np.ndarray, label: str, slot_id: int) -> None:
        """Kırpılmış görüntüyü kaydet."""
        timestamp = int(time.time() * 1000)
        filename = f"slot{slot_id}_{timestamp}.jpg"
        path = self._output_dir / label / filename
        cv2.imwrite(str(path), image)
        self._counts[label] += 1

    def collect_from_frame(self, frame: np.ndarray) -> bool:
        """Tek bir kareden tüm slotları kırp ve etiketle.

        Returns:
            False ise kullanıcı çıkmak istiyor
        """
        for slot in self._config.slots:
            # Slot'u kırp ve boyutlandır
            cropped = crop_polygon(frame, slot.polygon)
            resized = resize(cropped, self._crop_size)

            # Büyük göster
            display = cv2.resize(resized, (256, 256), interpolation=cv2.INTER_NEAREST)

            # Bilgi ekle
            label_text = f"Slot {slot.label} | E:empty O:occupied S:skip Q:quit"
            cv2.putText(display, label_text, (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

            cv2.imshow("Etiketle", display)
            key = cv2.waitKey(0) & 0xFF

            if key == ord("e"):
                self._save_crop(resized, "empty", slot.id)
                logger.debug("Slot %s -> empty", slot.label)

            elif key == ord("o"):
                self._save_crop(resized, "occupied", slot.id)
                logger.debug("Slot %s -> occupied", slot.label)

            elif key == ord("s"):
                self._counts["skipped"] += 1
                logger.debug("Slot %s -> atlandı", slot.label)

            elif key == ord("q") or key == 27:
                return False

        return True

    def print_stats(self) -> None:
        """Toplanan veri istatistiklerini yazdır."""
        print(f"\nVeri toplama tamamlandı:")
        print(f"  Empty:    {self._counts['empty']}")
        print(f"  Occupied: {self._counts['occupied']}")
        print(f"  Atlanan:  {self._counts['skipped']}")
        print(f"  Toplam:   {sum(self._counts.values())}")
