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
from park_slot.utils.image import crop_polygon_warped, equalize_lighting

logger = logging.getLogger(__name__)

PREVIEW_HEIGHT = 320


class DataCollector:
    """Slot kırpma ve etiketleme aracı."""

    def __init__(
        self,
        slot_config_path: str | Path,
        output_dir: str | Path = "data/labeled",
        crop_size: int = 64,
        slot_filter: str | None = None,
    ):
        """
        Args:
            slot_filter: Verilirse (örn. "S10") sadece o etiketteki slot
                gösterilir, diğerleri otomatik atlanır - belirli bir slot
                için hedefli veri toplarken (örn. ağaç dalı gibi nadir bir
                görünüm desenini yakalamak için) her karede 13+ alakasız
                slotu tek tek 's' ile geçmek zorunda kalmamak için.
        """
        self._config = self._load_config(slot_config_path)
        self._output_dir = Path(output_dir)
        self._crop_size = crop_size
        self._slot_filter = slot_filter
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

    def _build_raw_bbox_view(self, frame: np.ndarray, slot, height: int = PREVIEW_HEIGHT, padding_ratio: float = 0.6) -> np.ndarray:
        """Slotun etrafındaki ham (perspektif düzeltilmemiş, gerçek oranlı)
        dikdörtgen bölgeyi, biraz pay bırakarak kırp.

        Neden gerekli: crop_polygon_warped'ın ürettiği kare (modelin gördüğü
        şey) uzak/dar slotlarda zaten çok az gerçek piksel içeriyor; onu
        büyütmek yeni detay yaratmıyor, sadece bulanıklaştırıyor - insan
        gözüyle "araç var mı" diye ayırt etmek zorlaşıyor. Burada bunun
        yerine ham karedeki gerçek bölgeyi (çevresindeki bağlamla birlikte,
        oranlar bozulmadan) gösteriyoruz - sadece ekranda onay için, kaydedilen
        eğitim görüntüsü (modele giden) hâlâ crop_polygon_warped çıktısı.
        """
        pts = np.array(slot.polygon, dtype=np.float32)
        x0, y0 = pts[:, 0].min(), pts[:, 1].min()
        x1, y1 = pts[:, 0].max(), pts[:, 1].max()
        bw, bh = x1 - x0, y1 - y0
        pad_x = max(bw * padding_ratio, 15)
        pad_y = max(bh * padding_ratio, 15)

        fh, fw = frame.shape[:2]
        cx0 = int(max(x0 - pad_x, 0))
        cy0 = int(max(y0 - pad_y, 0))
        cx1 = int(min(x1 + pad_x, fw))
        cy1 = int(min(y1 + pad_y, fh))

        crop = frame[cy0:cy1, cx0:cx1].copy()
        shifted = (pts - [cx0, cy0]).astype(np.int32)
        cv2.polylines(crop, [shifted], True, (0, 0, 255), 2)

        ch, cw = crop.shape[:2]
        scale = height / ch
        crop = cv2.resize(crop, (max(int(cw * scale), 1), height), interpolation=cv2.INTER_CUBIC)
        cv2.putText(crop, "HAM (gercek oranli)", (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
        return crop

    def _build_context_view(self, frame: np.ndarray, current_slot, height: int = 256) -> np.ndarray:
        """Tüm karenin küçük bir önizlemesini, o anki slot parlak renkte
        vurgulanmış olarak çiz - çok sayıda (örn. 20+) yakın/küçük slot
        olunca sadece "Slot S15" yazısından hangisi olduğunu kestirmek zor
        oluyor, bu görsel bağlam onu netleştirir.
        """
        h, w = frame.shape[:2]
        scale = height / h
        context = cv2.resize(frame, (int(w * scale), height))

        for slot in self._config.slots:
            pts = (np.array(slot.polygon, dtype=np.float32) * scale).astype(np.int32)
            if slot.id == current_slot.id:
                cv2.polylines(context, [pts], True, (0, 255, 255), 2)
            else:
                cv2.polylines(context, [pts], True, (0, 120, 0), 1)

        label = current_slot.label or f"S{current_slot.id}"
        cv2.putText(context, f"-> {label}", (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        return context

    def _save_crop(self, image: np.ndarray, label: str, slot_id: int, captured_at: float | None = None) -> None:
        """Kırpılmış görüntüyü kaydet."""
        timestamp = int((captured_at if captured_at is not None else time.time()) * 1000)
        filename = f"slot{slot_id}_{timestamp}.jpg"
        path = self._output_dir / label / filename
        cv2.imwrite(str(path), image)
        self._counts[label] += 1

    def collect_from_frame(self, frame: np.ndarray, captured_at: float | None = None) -> bool:
        """Tek bir kareden tüm slotları kırp ve etiketle.

        Args:
            frame: İşlenecek kare
            captured_at: Karenin gerçek/tahmini yakalama zamanı (unix). Verilmezse
                kaydetme anı (time.time()) kullanılır.

        Returns:
            False ise kullanıcı çıkmak istiyor
        """
        frame = equalize_lighting(frame)

        for slot in self._config.slots:
            if self._slot_filter is not None and slot.label != self._slot_filter:
                continue

            # Slot'u kırp + perspektif düzelt (inference ile aynı yöntem - bkz. crop_polygon_warped)
            # NOT: kaydedilen/eğitime giden görüntü budur - aşağıdaki "ham" ve
            # "bağlam" görünümleri sadece ekranda insan onayı için, kaydedilmez.
            resized = crop_polygon_warped(frame, slot.polygon, self._crop_size)

            raw_view = self._build_raw_bbox_view(frame, slot)

            model_input = cv2.resize(resized, (PREVIEW_HEIGHT, PREVIEW_HEIGHT), interpolation=cv2.INTER_NEAREST)
            cv2.putText(model_input, "MODEL GIRDISI", (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            context = self._build_context_view(frame, slot, height=PREVIEW_HEIGHT)

            combined = np.hstack([raw_view, model_input, context])
            label_text = f"Slot {slot.label} | E:empty O:occupied S:skip Q:quit"
            cv2.putText(combined, label_text, (5, combined.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            cv2.imshow("Etiketle", combined)
            key = cv2.waitKey(0) & 0xFF

            if key == ord("e"):
                self._save_crop(resized, "empty", slot.id, captured_at)
                logger.debug("Slot %s -> empty", slot.label)

            elif key == ord("o"):
                self._save_crop(resized, "occupied", slot.id, captured_at)
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
