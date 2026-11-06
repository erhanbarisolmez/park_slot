"""Veri toplama aracı.

Kullanım:
    # --source verilmezse --slots dosyasındaki kayıtlı source_url kullanılır
    # (tools/annotate.py ile kamera ilk kez işaretlendiğinde otomatik kaydedilir)
    python tools/collect_data.py --slots config/slots/cam1.json
    python tools/collect_data.py --source video.mp4 --slots config/slots/cam1.json
    python tools/collect_data.py --source https://youtube.com/watch?v=... --slots config/slots/cam1.json

    # Canlı uçtan 60 dakika önceki tek bir kareyi etiketle
    python tools/collect_data.py --slots config/slots/cam1.json --minutes-ago 60

    # 3 saat önceden başlayıp 2 saat öncesine kadar, 30 dakikada bir örnek al
    python tools/collect_data.py --slots config/slots/cam1.json \\
        --minutes-ago 180 --window-minutes 60 --interval 1800
"""

import argparse
import json
import sys
import time
from datetime import datetime

from park_slot.capture.file import FileSource
from park_slot.capture.youtube import YouTubeSource
from park_slot.capture.youtube_history import (
    compute_target_sequence,
    fetch_frame_at_sequence,
    get_live_state,
    resolve_dash_url,
)
from park_slot.training.collector import DataCollector


def _print_captured_at(captured_at: float) -> None:
    print(f"Kare zamanı: {datetime.fromtimestamp(captured_at):%Y-%m-%d %H:%M:%S}")


def _run_live(source, collector: DataCollector, interval: float) -> bool:
    """Canlı akıştan sürekli kare çek ve etiketle.

    Her kare için bağlantı YENİDEN açılır (sürekli açık tutup seyrek
    aralıklarla read() çağırmak yerine). Neden: sürekli izleme pipeline'ı
    (0.2s'de bir okur) OpenCV/FFmpeg'in dahili arabelleğini sürekli
    boşalttığı için sorun yaşamıyor; ama burada birkaç saniyede bir tek
    read() çağrısı arabellek geride kaldığı için hep aynı/eski kareyi
    döndürebiliyor (gerçekte görüldü: "S10 için hep aynı kare geldi").
    Veri toplama aracı zaten yavaş/manuel etiketleme hızında çalıştığı için
    her anlık görüntü için birkaç saniyelik yeniden bağlanma maliyeti kabul
    edilebilir - karşılığında her zaman gerçekten taze bir kare garanti edilir.

    Returns:
        True: kaynak tükendi (normal bitiş). False: kullanıcı q/ESC ile çıktı.
    """
    while True:
        source.open()
        try:
            frame = source.read()
        finally:
            source.close()

        if frame is None:
            print("Kare okunamadı, çıkılıyor...")
            return True

        captured_at = time.time()
        _print_captured_at(captured_at)

        should_continue = collector.collect_from_frame(frame, captured_at=captured_at)
        if not should_continue:
            return False

        time.sleep(interval)

    return True


def _run_historical(url: str, collector: DataCollector, minutes_ago: float, window_minutes: float, interval: float) -> bool:
    """DVR/DASH geçmişinden belirli bir zaman penceresini tara.

    Returns:
        True: pencere sonuna kadar tarandı (normal bitiş). False: kullanıcı q/ESC ile çıktı.
    """
    height = 720
    live_sq, fragment_seconds = get_live_state(url, height)
    print(f"Akışın fragman süresi: {fragment_seconds:.1f}s (otomatik tespit edildi)")
    dash_url = resolve_dash_url(url, height)

    start_sq = compute_target_sequence(live_sq, minutes_ago, fragment_seconds)
    end_sq = compute_target_sequence(live_sq, max(minutes_ago - window_minutes, 0), fragment_seconds)
    step = max(1, round(interval / fragment_seconds))

    for sq in range(start_sq, end_sq + 1, step):
        try:
            frame = fetch_frame_at_sequence(dash_url, sq)
        except RuntimeError as e:
            print(f"Kare atlandı: {e}")
            continue

        captured_at = time.time() - (live_sq - sq) * fragment_seconds
        _print_captured_at(captured_at)

        should_continue = collector.collect_from_frame(frame, captured_at=captured_at)
        if not should_continue:
            return False

    return True


def main():
    parser = argparse.ArgumentParser(description="Park slotu veri toplama aracı")
    parser.add_argument(
        "--source", default=None,
        help="Video dosyası veya YouTube URL (verilmezse --slots dosyasındaki kayıtlı source_url kullanılır)",
    )
    parser.add_argument("--slots", required=True, help="Slot konfigürasyon JSON dosyası")
    parser.add_argument("--output", default="data/labeled", help="Çıktı klasörü")
    parser.add_argument("--interval", type=float, default=5.0, help="Kareler arası bekleme/aralık (saniye)")
    parser.add_argument(
        "--slot", default=None,
        help="Sadece bu etiketteki slotu göster (örn. S10) - diğerleri otomatik atlanır, hedefli veri toplama için",
    )
    parser.add_argument(
        "--minutes-ago", type=float, default=None,
        help="Canlı uçtan kaç dakika öncesinden başlanacak (verilmezse canlı mod)",
    )
    parser.add_argument(
        "--window-minutes", type=float, default=0,
        help="--minutes-ago ile birlikte: o noktadan itibaren ileriye doğru kaç dakikalık pencere taransın",
    )
    args = parser.parse_args()

    if args.window_minutes and args.minutes_ago is None:
        parser.error("--window-minutes sadece --minutes-ago ile birlikte kullanılabilir")

    source_arg = args.source
    if source_arg is None:
        with open(args.slots, "r", encoding="utf-8") as f:
            source_arg = json.load(f).get("source_url", "")
        if not source_arg:
            parser.error(
                "--source verilmedi ve slot konfigürasyonunda kayıtlı source_url yok. "
                "--source ile belirtin veya önce tools/annotate.py ile URL'i kaydedin."
            )
        print(f"Kaynak slot dosyasından alındı: {source_arg}")

    collector = DataCollector(args.slots, output_dir=args.output, slot_filter=args.slot)

    print(f"Kaynak: {source_arg}")
    print(f"Slotlar: {args.slots}")
    print(f"Çıktı: {args.output}")

    completed = True
    try:
        if args.minutes_ago is not None:
            print(f"Mod: geçmiş ({args.minutes_ago} dakika önce, pencere: {args.window_minutes} dakika)")
            print("Başlatılıyor...\n")
            completed = _run_historical(source_arg, collector, args.minutes_ago, args.window_minutes, args.interval)
        else:
            print(f"Mod: canlı | Aralık: {args.interval}s")
            print("Başlatılıyor...\n")
            if source_arg.startswith("http"):
                source = YouTubeSource(source_arg)
            else:
                source = FileSource(source_arg, loop=True)
            completed = _run_live(source, collector, args.interval)
    finally:
        collector.print_stats()

    if not completed:
        # Kullanıcı q/ESC ile çıktı - sweep script'lerinin (örn. collect_sweep.sh)
        # kalan zaman noktalarını atlayıp durabilmesi için ayrı bir exit code dön.
        print("\nKullanıcı çıktı (q/ESC).")
        sys.exit(2)


if __name__ == "__main__":
    main()
