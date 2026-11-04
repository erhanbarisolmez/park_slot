"""Performans metrikleri: FPS, latency, sayaçlar.

Pipeline'ın ne kadar hızlı çalıştığını ölçer.
API'den /health endpoint'inde raporlanır.
"""

import logging
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)


class Monitor:
    """FPS ve latency takip eden monitor."""

    def __init__(self, window_size: int = 30):
        """
        Args:
            window_size: Son kaç ölçümü tutacağız (hareketli ortalama)
        """
        self._lock = threading.Lock()
        self._frame_times: deque[float] = deque(maxlen=window_size)
        self._inference_times: deque[float] = deque(maxlen=window_size)
        self._frame_count: int = 0
        self._start_time: float = time.time()

    def record_frame(self, duration: float) -> None:
        """Bir kare işleme süresini kaydet.

        Args:
            duration: Saniye cinsinden toplam işleme süresi
        """
        with self._lock:
            self._frame_times.append(duration)
            self._frame_count += 1

    def record_inference(self, duration: float) -> None:
        """Bir inference süresini kaydet.

        Args:
            duration: Saniye cinsinden inference süresi
        """
        with self._lock:
            self._inference_times.append(duration)

    @property
    def fps(self) -> float:
        """Son ölçümlere göre FPS (frames per second)."""
        with self._lock:
            if not self._frame_times:
                return 0.0
            avg = sum(self._frame_times) / len(self._frame_times)
            return 1.0 / avg if avg > 0 else 0.0

    @property
    def avg_frame_time(self) -> float:
        """Ortalama kare işleme süresi (ms)."""
        with self._lock:
            if not self._frame_times:
                return 0.0
            return (sum(self._frame_times) / len(self._frame_times)) * 1000

    @property
    def avg_inference_time(self) -> float:
        """Ortalama inference süresi (ms)."""
        with self._lock:
            if not self._inference_times:
                return 0.0
            return (sum(self._inference_times) / len(self._inference_times)) * 1000

    @property
    def total_frames(self) -> int:
        """Toplam işlenen kare sayısı."""
        return self._frame_count

    @property
    def uptime(self) -> float:
        """Çalışma süresi (saniye)."""
        return time.time() - self._start_time

    def get_stats(self) -> dict:
        """Tüm metrikleri sözlük olarak döndür."""
        return {
            "fps": round(self.fps, 1),
            "avg_frame_time_ms": round(self.avg_frame_time, 1),
            "avg_inference_time_ms": round(self.avg_inference_time, 1),
            "total_frames": self.total_frames,
            "uptime_seconds": round(self.uptime, 0),
        }
