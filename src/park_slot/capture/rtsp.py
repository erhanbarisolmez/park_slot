"""RTSP kamera desteği - gerçek (kimlik doğrulamalı) IP kameralar için.

URL genelde rtsp://kullanici:sifre@ip:port/yol formatında, kimlik bilgisi
içerir - loglara/hata mesajlarına ASLA çözümlenmiş haliyle yazılmaz (bkz.
_mask_credentials), sadece maskelenmiş hali görünür. Gerçek URL'nin
${VAR} yer tutucularından çözümlenmesi çağıran tarafın sorumluluğu
(bkz. park_slot.utils.env.resolve_env_placeholders) - bu sınıf zaten
çözümlenmiş, kullanıma hazır bir URL alır.
"""

import logging
import re

import cv2
import numpy as np

from park_slot.settings import get_cpu_thread_limit

logger = logging.getLogger(__name__)

_CREDENTIALS_RE = re.compile(r"://[^@/]+@")


def _mask_credentials(url: str) -> str:
    """Log/hata mesajlarında göstermek için url'deki kullanici:sifre@ kısmını maskele."""
    return _CREDENTIALS_RE.sub("://***:***@", url)


class RTSPSource:
    """RTSP kameradan kare okuyan kaynak."""

    def __init__(self, url: str):
        self._url = url
        self._cap: cv2.VideoCapture | None = None

    def open(self) -> None:
        self._cap = cv2.VideoCapture(self._url)
        # FFmpeg decode thread'leri cv2.setNumThreads()/CPU_LIMIT'ten
        # bağımsız, host'un tüm çekirdeklerine göre default'lanır.
        self._cap.set(cv2.CAP_PROP_N_THREADS, get_cpu_thread_limit())
        if not self._cap.isOpened():
            raise RuntimeError(f"RTSP bağlantısı açılamadı: {_mask_credentials(self._url)}")
        logger.info("RTSP bağlantısı kuruldu: %s", _mask_credentials(self._url))

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
