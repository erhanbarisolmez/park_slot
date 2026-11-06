"""YouTube canlı yayınında geçmişe zaman sıçramasıyla kare çekme.

YouTube'un web player'ındaki geri/ileri sarma özelliği, düz HLS formatının
(~30 saniyelik DVR penceresi, seek desteklenmiyor) değil, DASH canlı
fragmanlarının kullandığı `&sq=<sequence>` parametresiyle çalışıyor.
`sq` numarasını canlı uçtan geriye doğru kaydırarak günler öncesine ait
gerçek kareler indirilebiliyor (ampirik olarak en az 6 gün geriye test edildi).

ÖNEMLİ: fragman süresi yayından yayına değişir (bazı akışlarda 2.0s, bazılarında
5.0s) - bu yüzden sabit kodlanmıyor, her çağrıda manifestteki #EXTINF/
#EXT-X-TARGETDURATION etiketinden okunuyor (bkz. parse_fragment_seconds).
Yanlış/sabit bir değer kullanmak minutes_ago'nun gerçekte kaç dakika geriye
gittiğini sistematik olarak saptırır (örn. 2.0 varsayılırken gerçek 5.0 olan
bir akışta, istenenin ~2.5 katı kadar geriye gidilir).

Bu modül, sürekli açık bir stream yerine tek seferlik "X dakika önce ne
vardı" tipinde stateless bir çağrı sunuyor; bu yüzden FrameSource
Protocol'üne (open/read/close oturumu) uymuyor ve ayrı tutuluyor.
"""

import logging
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from park_slot.capture.youtube import YTDLP_JS_CHALLENGE_ARGS, find_ytdlp, subprocess_env

logger = logging.getLogger(__name__)

# Manifestten fragman süresi okunamazsa kullanılacak son çare varsayılan (saniye)
DEFAULT_FRAGMENT_SECONDS = 2.0
# Geriye dönük uyumluluk için eski isim
FRAGMENT_SECONDS = DEFAULT_FRAGMENT_SECONDS

_MEDIA_SEQUENCE_RE = re.compile(r"#EXT-X-MEDIA-SEQUENCE:(\d+)")
_EXTINF_RE = re.compile(r"#EXTINF:([\d.]+)")
_TARGET_DURATION_RE = re.compile(r"#EXT-X-TARGETDURATION:(\d+)")


def compute_target_sequence(
    live_sq: int, minutes_ago: float, fragment_seconds: float = DEFAULT_FRAGMENT_SECONDS
) -> int:
    """Canlı sequence numarasından minutes_ago kadar geriye giderek hedef sequence'i hesapla."""
    return live_sq - round(minutes_ago * 60 / fragment_seconds)


def parse_fragment_seconds(manifest_text: str, default: float = DEFAULT_FRAGMENT_SECONDS) -> float:
    """Manifestteki gerçek fragman süresini oku (#EXTINF öncelikli, yoksa #EXT-X-TARGETDURATION).

    Yayından yayına değişebildiği için (örn. 2.0s veya 5.0s) sabit kodlanmaz.
    """
    match = _EXTINF_RE.search(manifest_text)
    if match:
        return float(match.group(1))

    match = _TARGET_DURATION_RE.search(manifest_text)
    if match:
        return float(match.group(1))

    logger.warning("Manifestte fragman süresi bulunamadı, varsayılan kullanılıyor: %.1fs", default)
    return default


def _run_ytdlp_get_url(url: str, format_selector: str, live_from_start: bool = False) -> str:
    """yt-dlp --get-url ile bir format için stream URL'i çöz."""
    ytdlp = find_ytdlp()
    cmd = [ytdlp, "--get-url", "-f", format_selector, "--no-playlist", *YTDLP_JS_CHALLENGE_ARGS]
    if live_from_start:
        cmd.append("--live-from-start")
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=subprocess_env())
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp hatası: {result.stderr.strip()}")

    resolved = result.stdout.strip()
    if not resolved:
        raise RuntimeError("yt-dlp boş URL döndürdü")
    return resolved


def _fetch_live_manifest_text(url: str, height: int) -> str:
    """Normal (near-live) HLS manifestinin ham metnini al."""
    manifest_url = _run_ytdlp_get_url(url, f"best[height<={height}]/bestvideo[height<={height}]/best")
    with urllib.request.urlopen(manifest_url, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def get_live_state(url: str, height: int) -> tuple[int, float]:
    """Mevcut canlı sequence numarasını ve bu akışın gerçek fragman süresini oku.

    Fragman süresi yayından yayına değiştiği için (2.0s, 5.0s, ...) her
    çağrıda manifestten ölçülür, sabit varsayılmaz.

    Returns:
        (live_sq, fragment_seconds)
    """
    manifest_text = _fetch_live_manifest_text(url, height)

    match = _MEDIA_SEQUENCE_RE.search(manifest_text)
    if not match:
        raise RuntimeError("Manifestte EXT-X-MEDIA-SEQUENCE bulunamadı")
    live_sq = int(match.group(1))

    fragment_seconds = parse_fragment_seconds(manifest_text)
    return live_sq, fragment_seconds


def get_live_sequence(url: str, height: int) -> int:
    """Normal (near-live) HLS manifestinden mevcut canlı sequence numarasını oku.

    Not: fragman süresine de ihtiyacın varsa (minutes_ago hesaplaması için
    gerekli) get_live_state() kullan - burada ikinci bir manifest isteği
    yapılmasını önler.
    """
    live_sq, _ = get_live_state(url, height)
    return live_sq


def resolve_dash_url(url: str, height: int) -> str:
    """DASH (video-only, geçmişe erişilebilir) format URL'ini çöz.

    AV1 (av01) formatları bazı sistemlerde donanım/yazılım ile çözülemiyor
    (tek bir fragman init segment'siz decode edilince hata veriyor) -
    bu yüzden H.264 (avc1) açıkça tercih ediliyor.
    """
    selector = f"bestvideo[height<={height}][vcodec^=avc1]/bestvideo[height<={height}]"
    return _run_ytdlp_get_url(url, selector, live_from_start=True)


def fetch_frame_at_sequence(dash_url: str, sequence: int) -> np.ndarray:
    """Belirli bir sequence numarasındaki DASH fragmanını indir ve ilk kareyi çöz."""
    fragment_url = f"{dash_url}&sq={sequence}"

    try:
        with urllib.request.urlopen(fragment_url, timeout=15) as resp:
            fragment_bytes = resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Bu kadar geriye gidilemiyor, segment mevcut değil (sq={sequence}): {e}") from e

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp.write(fragment_bytes)
            tmp_path = tmp.name

        cap = cv2.VideoCapture(tmp_path)
        try:
            ret, frame = cap.read()
        finally:
            cap.release()

        if not ret or frame is None:
            raise RuntimeError(f"Fragman çözülemedi (sq={sequence})")
        return frame
    finally:
        if tmp_path is not None:
            Path(tmp_path).unlink(missing_ok=True)


def fetch_historical_frame(url: str, minutes_ago: float, height: int = 720) -> tuple[np.ndarray, float]:
    """minutes_ago kadar geriye giderek canlı yayından bir kare çeker.

    Args:
        url: YouTube canlı yayın URL'i
        minutes_ago: Şu andan kaç dakika öncesi (0 = canlı uç)
        height: İstenen çözünürlük yüksekliği

    Returns:
        (frame, tahmini_capture_unix_zamani)
    """
    live_sq, fragment_seconds = get_live_state(url, height)
    target_sq = compute_target_sequence(live_sq, minutes_ago, fragment_seconds)

    dash_url = resolve_dash_url(url, height)
    frame = fetch_frame_at_sequence(dash_url, target_sq)

    estimated_capture_time = time.time() - minutes_ago * 60
    logger.info(
        "Geçmiş kare alındı: %.1f dakika önce (sq=%d, fragman=%.1fs)",
        minutes_ago, target_sq, fragment_seconds,
    )
    return frame, estimated_capture_time
