"""Aşama 7 (PR b): NTP senkron kontrolü.

Saati bozuk bir pod'un ileri-kaymış bir epoch yayınlayıp bir slotu kalıcı
olarak "kazanmasını" (backend enforce modda gerçek zaman o noktaya erişene
kadar hiçbir event onu geçemez) kaynağında önler. Backend'deki ayna kontrol
(ParkOccupancyManager.FUTURE_EPOCH_TOLERANCE_MILLIS) ikinci, bağımsız bir
güvenlik ağı - bu ikisi birbirini tamamlar, biri bypass edilse diğeri kalır.
"""

import logging
from typing import Callable

import ntplib

logger = logging.getLogger(__name__)


def check_clock_sync(
    ntp_server: str,
    threshold_seconds: float,
    ntp_client_factory: Callable[[], "ntplib.NTPClient"] = ntplib.NTPClient,
    request_timeout_seconds: float = 2.0,
) -> bool:
    """True = senkron eşik dahilinde (yayına izin ver). False = drift eşiği
    aştı VEYA NTP sunucusuna erişilemedi - fail-safe: "senkron doğrulanamıyor"
    ile "senkron bozuk" aynı sonuca (yayını durdur) götürür, çünkü ikisini
    ayırt edemediğimiz durumda riskli taraf hep "bozuk kabul et" olmalı.

    request_timeout_seconds: code-review bulgusu - bu fonksiyon OutboxWorker'ın
    arka plan thread'inde senkron çalışıyor (bkz. OutboxWorker._run); ntplib'in
    kendi varsayılanı (5s) OutboxWorker.stop(timeout=5.0)'ın tam süresini
    kaplayabilirdi. Daha düşük bir varsayılanla worst-case bloklanma süresi
    sınırlanır (tamamen ortadan kalkmaz - socket I/O yine de kesilemez, ama
    pencere küçülür).
    """
    try:
        client = ntp_client_factory()
        response = client.request(ntp_server, version=3, timeout=request_timeout_seconds)
    except Exception as e:
        logger.error("NTP sorgusu başarısız (server=%s): %s", ntp_server, e)
        return False

    offset = response.offset
    if abs(offset) > threshold_seconds:
        logger.error(
            "Saat senkron eşiği aşıldı (server=%s, offset=%.3fs, threshold=%.3fs)",
            ntp_server, offset, threshold_seconds,
        )
        return False
    return True
