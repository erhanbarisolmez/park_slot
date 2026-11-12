"""torchvision tabanlı araç tespiti - per-slot CNN'in "açılı kamerada araç
gövdesi komşu slot'a taşıyor" sorununu düzeltmek için.

Neden ultralytics/YOLO DEĞİL: ultralytics paketi (ve YOLOv8/v5 kodu) AGPL-3.0
lisanslı - ağ üzerinden servis olarak sunulan (bizim FastAPI servisimiz gibi)
yazılımlarda da kaynak kodun açılmasını zorunlu kılıyor, ticari/kapalı kaynak
kullanım için (Ultralytics'in ücretli Enterprise License'ı olmadan) uygun
değil. Bunun yerine zaten projede kurulu olan torchvision'ın (BSD-3-Clause,
tamamen izinli) KENDİ nesne tespit modellerini kullanıyoruz - yeni bağımlılık
yok, lisans belirsizliği yok.

Model seçimi: SSDLite320 (daha hızlı, ~130ms/kare) gece sahnelerinde aracı
tamamen kaçırdı (en yüksek güven %19) - Faster R-CNN MobileNetV3-320-FPN
(~450ms/kare) doğru tespit ediyor (%55-61), bu yüzden o seçildi. 450ms,
1.5s'lik frame_interval'a rahatça sığıyor.

Neden gerekli: per-slot CNN her slot polygonunu bağımsız kırpıp sınıflandırır;
açılı/alçak kameralarda uzun bir aracın gövdesi birden fazla komşu slot
polygonuna görsel olarak taşıyabilir, bu da tek bir aracın 3-4 slotu birden
"dolu" göstermesine yol açar (bkz. cam2 S6-S9 vakası). Homografi/kuşbakışı
projeksiyon bunu çözmez çünkü sadece zemin düzlemini (z=0) doğru haritalar;
aracın tavanı/gövdesi zeminden yükseklikte olduğu için projeksiyonda kayar
("relief displacement" - fotogrametri terimi, uydu görüntülerinde binaların
"yatık" görünmesiyle aynı sebep).

Çözüm: aracın gövdesine değil, TEKERLEĞİNİN ZEMİNE DEĞDİĞİ NOKTAYA bakmak.
Bu nokta zeminde olduğu için projeksiyon/yükseklik sorunundan etkilenmez -
araç kaç slota görsel taşarsa taşsın, zemin noktası sadece gerçekte durduğu
TEK slotun polygonuna düşer.

CNN ile ilişki (bkz. inference/pipeline.py _process_frame): CNN sonucu
tamamen atılmaz, SADECE POZİTİF yönde düzeltilir:
- Dedektör bir aracı bir slota zemin noktasıyla atadıysa -> o slot OCCUPIED
  (CNN ne derse desin, geometrik sinyal daha güvenilir).
- Dedektör bu slotla ilgili hiçbir şey görmüyorsa -> CNN'in kararına aynen
  güvenilir (dedektörün COCO sınıflarına girmeyen/çok uzak/karanlıkta
  kaçırdığı araçlar için fallback).

Bir de "aracın kutusu bu slota görsel taşıyor ama zemin noktası başka slota
düştü -> CNN'in occupied kararını EMPTY'ye çevir" şeklinde bir suppression
dalı denendi (komşu-taşması vakalarını yakalamak için) ama kaldırıldı:
ground_offset sabiti (kutunun üstten %85'i = "zemin noktası") kamera açısına
göre her 3 kamerada güvenilir şekilde doğru slota düşmüyor - bu da cam1
S2/S3 ve cam3 S3/S5/S6'da GERÇEKTEN dolu slotları yanlışlıkla boşalttı.
Yanlış "boş" (gerçekte dolu), yanlış "dolu"dan daha zararlı kabul edildi
(kullanıcıyı boşuna o slota yönlendirir), bu yüzden sadece pozitif düzeltme
bırakıldı.
"""

import logging
from dataclasses import dataclass

import numpy as np
import torch

logger = logging.getLogger(__name__)

# torchvision COCO sınıf id'leri (91 sınıflık orijinal indeksleme, YOLO'nun
# kompakt 80 sınıflık indekslemesinden FARKLI): 3=car, 4=motorcycle, 6=bus, 8=truck
VEHICLE_CLASS_IDS = {3, 4, 6, 8}

_model_cache: dict[str, "object"] = {}


@dataclass
class VehicleDetection:
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    ground_point: tuple[float, float]
    confidence: float


class VehicleDetector:
    """torchvision Faster R-CNN MobileNetV3 (COCO önceden eğitilmiş, kendi
    eğitimimiz yok) ile araç tespiti. BSD-3-Clause lisanslı - bkz. modül
    docstring'i (ultralytics/YOLO'nun AGPL-3.0 lisansından kaçınmak için)."""

    def __init__(
        self,
        model_path: str = "fasterrcnn_mobilenet_v3_large_320_fpn",
        imgsz: int = 416,
        conf_threshold: float = 0.4,
        ground_offset: float = 0.85,
        device: str = "cpu",
    ):
        """
        Args:
            model_path: torchvision model adı (şu an tek desteklenen:
                fasterrcnn_mobilenet_v3_large_320_fpn - SSDLite320 denendi,
                gece sahnelerinde araçları kaçırdı, bkz. modül docstring'i)
            imgsz: Kullanılmıyor (torchvision modeli kendi iç boyutlandırmasını
                yapıyor) - ayarlar/config uyumluluğu için parametre korundu
            conf_threshold: Bu altındaki tespitler atılır
            ground_offset: Kutunun üstten bu oranda aşağısı "zemin noktası"
                sayılır (1.0 = kutunun tam altı; 0.85 kullanılıyor çünkü tam
                alt kenar genelde plaka/gölge gürültüsü içeriyor)
            device: cpu veya cuda
        """
        from torchvision.models.detection import (
            FasterRCNN_MobileNet_V3_Large_320_FPN_Weights,
            fasterrcnn_mobilenet_v3_large_320_fpn,
        )

        cache_key = f"{model_path}:{device}"
        if cache_key not in _model_cache:
            weights = FasterRCNN_MobileNet_V3_Large_320_FPN_Weights.DEFAULT
            model = fasterrcnn_mobilenet_v3_large_320_fpn(weights=weights)
            model.eval()
            model.to(device)
            _model_cache[cache_key] = model
        self._model = _model_cache[cache_key]
        self._device = device
        self._conf_threshold = conf_threshold
        self._ground_offset = ground_offset

    def detect(self, frame: np.ndarray) -> list[VehicleDetection]:
        """Karedeki araçları tespit et, her biri için zemin noktası hesapla.

        Modelin kendi NMS'i SINIF BAZINDA çalışır - aynı fiziksel aracı iki
        farklı sınıfla (örn. hem "car" hem "truck") ve çakışan kutularla iki
        kez işaretlemesini önlemez. Bu da ham tespit sayısını (ve
        get_vehicle_count()'u) gerçek araç sayısından fazla gösterir. Burada
        sınıf sınırı olmadan ek bir NMS geçişi uygulanıp bu çakışan
        kopyalar tek tespite indirgeniyor.
        """
        import cv2
        from torchvision.ops import nms

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        tensor = torch.from_numpy(rgb).permute(2, 0, 1).float().div(255.0).to(self._device)

        with torch.no_grad():
            output = self._model([tensor])[0]

        vehicle_mask = torch.isin(
            output["labels"], torch.tensor(sorted(VEHICLE_CLASS_IDS), device=self._device),
        ) & (output["scores"] >= self._conf_threshold)
        boxes = output["boxes"][vehicle_mask]
        scores = output["scores"][vehicle_mask]

        # Sınıf etiketinden bağımsız NMS: aynı aracı kapsayan çakışan
        # kutulardan sadece en yüksek güvenli olanı tutulur.
        keep = nms(boxes, scores, iou_threshold=0.4)

        detections = []
        for box, score in zip(boxes[keep], scores[keep]):
            x1, y1, x2, y2 = box.tolist()
            gx = (x1 + x2) / 2
            gy = y1 + (y2 - y1) * self._ground_offset
            detections.append(
                VehicleDetection(
                    bbox=(x1, y1, x2, y2),
                    ground_point=(gx, gy),
                    confidence=float(score),
                )
            )
        return detections


def assign_to_slots(
    detections: list[VehicleDetection],
    slot_polygons: dict[int, list[list[int]]],
) -> set[int]:
    """Tespit edilen araçları slot'lara eşle.

    Returns:
        assigned_ids: en az bir aracın zemin noktası bu slot'un içinde
            (yüksek güvenilirlik - OCCUPIED kabul edilir)
    """
    import cv2

    assigned_ids: set[int] = set()

    for slot_id, polygon in slot_polygons.items():
        poly_arr = np.array(polygon, dtype=np.int32)

        for det in detections:
            if cv2.pointPolygonTest(poly_arr, det.ground_point, False) >= 0:
                assigned_ids.add(slot_id)
                break

    return assigned_ids
