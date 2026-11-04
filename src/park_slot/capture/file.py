"""Lokal video dosyasından kare okuma.

Test ve geliştirme sırasında kullanılır.
YouTube/RTSP'ye gerek kalmadan pipeline'ı test edebilirsin.
"""

import logging
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


class FileSource:
    """Lokal video dosyasından kare okuyan kaynak.

    Kullanım:
        source = FileSource("test_video.mp4")
        source.open()
        while source.is_open():
            frame = source.read()
            if frame is None:
                break
            # frame ile işlem yap
        source.close()
    """

    def __init__(self, path: str | Path, loop: bool = False):
        """
        Args:
            path: Video dosyasının yolu
            loop: Video bitince başa sar (test için kullanışlı)
        """
        self._path = Path(path)
        self._loop = loop
        self._cap: cv2.VideoCapture | None = None

    def open(self) -> None:
        """Video dosyasını aç."""
        if not self._path.exists():
            raise FileNotFoundError(f"Video bulunamadı: {self._path}")

        self._cap = cv2.VideoCapture(str(self._path))
        if not self._cap.isOpened():
            raise RuntimeError(f"Video açılamadı: {self._path}")

        logger.info(
            "Video açıldı: %s (%.0f fps, %dx%d)",
            self._path.name,
            self._cap.get(cv2.CAP_PROP_FPS),
            int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )

    def read(self) -> np.ndarray | None:
        """Bir kare oku."""
        if self._cap is None:
            return None

        ret, frame = self._cap.read()

        if not ret:
            if self._loop:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self._cap.read()
                if not ret:
                    return None
                logger.debug("Video başa sarıldı")
                return frame
            return None

        return frame

    def is_open(self) -> bool:
        """Video açık mı?"""
        return self._cap is not None and self._cap.isOpened()

    def close(self) -> None:
        """Video dosyasını kapat."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("Video kapatıldı: %s", self._path.name)
