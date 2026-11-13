"""YouTube canlı yayından kare okuma.

yt-dlp'yi subprocess olarak kullanıyoruz çünkü:
- Python API'si kararsız ve sık değişiyor
- Subprocess izole: yt-dlp crash olsa bile ana process etkilenmez
- yt-dlp'yi bağımsız güncelleyebilirsin (pip install -U yt-dlp)
- Stream URL'i belirli süre sonra expire olur, otomatik yenileme gerekir
"""

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

from park_slot.settings import get_cpu_thread_limit
from park_slot.utils.retry import retry

logger = logging.getLogger(__name__)

# Stream URL bu kadar saniye sonra yenilenir
URL_REFRESH_INTERVAL = 3600  # 1 saat

# YouTube artık bazı videolarda ("n challenge") JS tabanlı bir bot-engelleme
# testi uyguluyor; yt-dlp bunu çözmek için bir JS runtime (deno) + çözücü
# script'i (ejs) gerektiriyor. Olmadan bazı videolar "not available" gibi
# yanıltıcı bir hatayla başarısız oluyor (gerçek sebep challenge çözülemedi).
YTDLP_JS_CHALLENGE_ARGS = ["--js-runtimes", "deno", "--remote-components", "ejs:github"]

# deno kullanıcı dotfile'larına dokunmadan (proje kodundan) bulunabilsin diye
# bilinen kurulum konumları PATH'e eklenir.
_EXTRA_PATH_DIRS = [
    str(Path.home() / ".npm-global" / "bin"),
    str(Path.home() / ".deno" / "bin"),
]


def subprocess_env() -> dict:
    """deno gibi araçları bulabilmesi için genişletilmiş PATH içeren ortam değişkenleri."""
    env = os.environ.copy()
    current_path = env.get("PATH", "")
    extra = os.pathsep.join(d for d in _EXTRA_PATH_DIRS if d not in current_path)
    env["PATH"] = f"{extra}{os.pathsep}{current_path}" if extra else current_path
    return env


def find_ytdlp() -> str:
    """yt-dlp executable yolunu bul."""
    # Önce venv/Scripts içinde ara
    venv_path = Path(sys.executable).parent / "yt-dlp.exe"
    if venv_path.exists():
        return str(venv_path)
    # Sonra PATH'te ara
    found = shutil.which("yt-dlp")
    if found:
        return found
    raise FileNotFoundError("yt-dlp bulunamadı. 'pip install yt-dlp' ile kurun.")


class YouTubeSource:
    """YouTube canlı yayından kare okuyan kaynak.

    Kullanım:
        source = YouTubeSource("https://www.youtube.com/watch?v=...")
        source.open()
        frame = source.read()
        source.close()
    """

    def __init__(self, url: str, resolution: list[int] | None = None):
        """
        Args:
            url: YouTube video/canlı yayın URL'i
            resolution: İstenen çözünürlük [width, height]
        """
        self._url = url
        self._resolution = resolution or [1280, 720]
        self._cap: cv2.VideoCapture | None = None
        self._stream_url: str = ""
        self._last_refresh: float = 0
        self._lock = threading.Lock()

    @retry(max_retries=3, base_delay=2.0, exceptions=(RuntimeError, subprocess.SubprocessError))
    def _get_stream_url(self) -> str:
        """yt-dlp ile gerçek stream URL'ini al."""
        ytdlp = find_ytdlp()
        height = self._resolution[1]
        cmd = [
            ytdlp,
            "--get-url",                    # Sadece URL döndür
            "-f", f"best[height<={height}]/bestvideo[height<={height}]/best",
            "--no-playlist",
            *YTDLP_JS_CHALLENGE_ARGS,
            self._url,
        ]

        logger.info("Stream URL alınıyor: %s", self._url)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env=subprocess_env(),
        )

        if result.returncode != 0:
            raise RuntimeError(f"yt-dlp hatası: {result.stderr.strip()}")

        stream_url = result.stdout.strip()
        if not stream_url:
            raise RuntimeError("yt-dlp boş URL döndürdü")

        logger.info("Stream URL alındı")
        return stream_url

    def _refresh_if_needed(self) -> None:
        """Stream URL expire olduysa yenile."""
        elapsed = time.time() - self._last_refresh
        if elapsed > URL_REFRESH_INTERVAL:
            logger.info("Stream URL yenileniyor (%.0fs geçti)", elapsed)
            self._open_stream()

    def _open_stream(self) -> None:
        """Stream URL al ve OpenCV ile aç."""
        # Eski bağlantıyı kapat
        if self._cap is not None:
            self._cap.release()

        self._stream_url = self._get_stream_url()
        self._cap = cv2.VideoCapture(self._stream_url)
        # Mümkünse arabelleği küçük tut - seyrek read() çağrılarında (örn.
        # tools/collect_data.py) eski/bayat kare dönmesini azaltır.
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # FFmpeg decode thread'leri cv2.setNumThreads()/CPU_LIMIT'ten bağımsız,
        # host'un tüm çekirdeklerine göre default'lanır - OpenCV build'i
        # destekliyorsa container limitine sabitle (desteklemiyorsa .set()
        # sessizce False döner, hata fırlatmaz).
        self._cap.set(cv2.CAP_PROP_N_THREADS, get_cpu_thread_limit())

        if not self._cap.isOpened():
            raise RuntimeError("OpenCV stream'i açamadı")

        self._last_refresh = time.time()
        logger.info("YouTube stream bağlantısı kuruldu")

    def open(self) -> None:
        """YouTube stream'e bağlan."""
        self._open_stream()

    def read(self) -> np.ndarray | None:
        """Bir kare oku. Bağlantı koparsa yeniden bağlanmayı dener."""
        with self._lock:
            if self._cap is None:
                return None

            self._refresh_if_needed()

            ret, frame = self._cap.read()
            if not ret:
                logger.warning("Kare okunamadı, yeniden bağlanılıyor...")
                try:
                    self._open_stream()
                    ret, frame = self._cap.read()
                    if not ret:
                        return None
                except Exception as e:
                    logger.error("Yeniden bağlanma başarısız: %s", e)
                    return None

            return frame

    def is_open(self) -> bool:
        """Stream açık mı?"""
        return self._cap is not None and self._cap.isOpened()

    def close(self) -> None:
        """Stream bağlantısını kapat."""
        with self._lock:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
                logger.info("YouTube stream kapatıldı")
