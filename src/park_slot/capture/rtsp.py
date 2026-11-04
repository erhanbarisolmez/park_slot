"""RTSP kamera desteği - ileride gerçek kameralar için.

Şimdilik iskelet, YouTube'dan RTSP'ye geçişte doldurulacak.
"""

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class RTSPSource:
    """RTSP kameradan kare okuyan kaynak."""

    def __init__(self, url: str):
        self._url = url
        self._cap: cv2.VideoCapture | None = None

    def open(self) -> None:
        self._cap = cv2.VideoCapture(self._url)
        if not self._cap.isOpened():
            raise RuntimeError(f"RTSP bağlantısı açılamadı: {self._url}")
        logger.info("RTSP bağlantısı kuruldu: %s", self._url)

    def read(self) -> np.ndarray | None:
        if self._cap is None:
            return None
        ret, frame = self._cap.read()
        return frame if ret else None

    def is_open(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("RTSP bağlantısı kapatıldı")
