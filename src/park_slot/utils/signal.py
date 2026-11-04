"""Graceful shutdown - uygulamayı düzgün kapatma."""

import logging
import signal
import threading

logger = logging.getLogger(__name__)


class ShutdownHandler:
    """Ctrl+C veya SIGTERM geldiğinde uygulamayı düzgün kapatır.

    Kullanım:
        shutdown = ShutdownHandler()

        while not shutdown.requested:
            # iş yap
            ...

        shutdown.wait()  # Cleanup bitene kadar bekle
    """

    def __init__(self):
        self._requested = threading.Event()
        self._callbacks: list = []

        # SIGINT (Ctrl+C) ve SIGTERM yakalama
        signal.signal(signal.SIGINT, self._handle)
        signal.signal(signal.SIGTERM, self._handle)

    def _handle(self, signum: int, frame) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Kapatma sinyali alındı: %s", sig_name)
        self._requested.set()

        # Kayıtlı callback'leri çalıştır
        for callback in self._callbacks:
            try:
                callback()
            except Exception as e:
                logger.error("Shutdown callback hatası: %s", e)

    @property
    def requested(self) -> bool:
        """Kapatma istendi mi?"""
        return self._requested.is_set()

    def register(self, callback) -> None:
        """Kapatma sırasında çalıştırılacak fonksiyon ekle."""
        self._callbacks.append(callback)

    def wait(self, timeout: float | None = None) -> bool:
        """Kapatma sinyali gelene kadar bekle."""
        return self._requested.wait(timeout=timeout)
