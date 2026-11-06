"""Production tahmin günlüğü ve proxy kalite/drift metrikleri.

Canlıda gerçek ground-truth (insan etiketi) olmadığı için gerçek doğruluk
ölçülemez. Bu modül StateStore'un onaylı durum DEĞİŞİKLİKLERİNİ (her ham
kareyi değil - bkz. StateStore._apply_hysteresis) kaydeder ve bunlardan
dolaylı/proxy sinyaller türetir:

- flip_rate_per_hour: saatte onaylı durum değişim sayısı. Bir slot için
  anormal yüksekse kararsız tahmin / bozuk polygon işareti olabilir.
- unknown_rate: durum değişimlerinin ne kadarı UNKNOWN'a düşüyor - güven
  çöküşü / olası drift sinyali.
- avg_confidence: son pencerenin ortalama güveni.

Bunlar "accuracy" değildir - sadece operasyonel anormallik sinyalleridir.
"""

import json
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from park_slot.annotation.schema import SlotState, SlotStatus

logger = logging.getLogger(__name__)


class PredictionLog:
    """Onaylı durum değişimlerini bellekte tutar, opsiyonel olarak JSONL'e yazar."""

    def __init__(self, log_path: Optional[str | Path] = None, max_events: int = 5000):
        self._lock = threading.Lock()
        self._events: deque[dict] = deque(maxlen=max_events)
        self._log_path = Path(log_path) if log_path else None
        if self._log_path is not None:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, state: SlotState) -> None:
        """StateStore.on_change callback'i olarak kullanılır."""
        event = {
            "timestamp": time.time(),
            "slot_id": state.id,
            "raw_status": state.raw_status.value,
            "confirmed_status": state.status.value,
            "confidence": state.confidence,
        }
        with self._lock:
            self._events.append(event)

        if self._log_path is not None:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event) + "\n")
            except OSError as e:
                logger.error("Tahmin günlüğü dosyaya yazılamadı: %s", e)

    def get_quality_metrics(self, window_seconds: float = 3600.0) -> dict:
        """Son `window_seconds` içindeki durum değişimlerinden proxy metrikler hesapla."""
        now = time.time()
        with self._lock:
            events = list(self._events)

        recent = [e for e in events if now - e["timestamp"] <= window_seconds]
        hours = window_seconds / 3600.0

        if not recent:
            return {
                "window_seconds": window_seconds,
                "state_change_count": 0,
                "flip_rate_per_hour": 0.0,
                "unknown_rate": 0.0,
                "avg_confidence": 0.0,
                "per_slot": {},
            }

        unknown_count = sum(1 for e in recent if e["confirmed_status"] == SlotStatus.UNKNOWN.value)
        avg_confidence = sum(e["confidence"] for e in recent) / len(recent)

        per_slot_events: dict[int, list[dict]] = {}
        for e in recent:
            per_slot_events.setdefault(e["slot_id"], []).append(e)

        per_slot = {
            slot_id: {
                "flip_rate_per_hour": round(len(evts) / hours, 2),
                "unknown_rate": round(
                    sum(1 for e in evts if e["confirmed_status"] == SlotStatus.UNKNOWN.value) / len(evts), 3,
                ),
                "avg_confidence": round(sum(e["confidence"] for e in evts) / len(evts), 3),
            }
            for slot_id, evts in per_slot_events.items()
        }

        return {
            "window_seconds": window_seconds,
            "state_change_count": len(recent),
            "flip_rate_per_hour": round(len(recent) / hours, 2),
            "unknown_rate": round(unknown_count / len(recent), 3),
            "avg_confidence": round(avg_confidence, 3),
            "per_slot": per_slot,
        }
