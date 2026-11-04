"""Veri toplama aracı.

Kullanım:
    python tools/collect_data.py --source video.mp4 --slots config/slots/cam1.json
    python tools/collect_data.py --source https://youtube.com/watch?v=... --slots config/slots/cam1.json
"""

import argparse
import time

from park_slot.capture.file import FileSource
from park_slot.capture.youtube import YouTubeSource
from park_slot.training.collector import DataCollector


def main():
    parser = argparse.ArgumentParser(description="Park slotu veri toplama aracı")
    parser.add_argument("--source", required=True, help="Video dosyası veya YouTube URL")
    parser.add_argument("--slots", required=True, help="Slot konfigürasyon JSON dosyası")
    parser.add_argument("--output", default="data/labeled", help="Çıktı klasörü")
    parser.add_argument("--interval", type=float, default=5.0, help="Kareler arası bekleme (saniye)")
    args = parser.parse_args()

    # Kaynak oluştur
    if args.source.startswith("http"):
        source = YouTubeSource(args.source)
    else:
        source = FileSource(args.source, loop=True)

    # Collector oluştur
    collector = DataCollector(args.slots, output_dir=args.output)

    print(f"Kaynak: {args.source}")
    print(f"Slotlar: {args.slots}")
    print(f"Çıktı: {args.output}")
    print(f"Aralık: {args.interval}s")
    print("Başlatılıyor...\n")

    source.open()

    try:
        while source.is_open():
            frame = source.read()
            if frame is None:
                print("Kare okunamadı, çıkılıyor...")
                break

            should_continue = collector.collect_from_frame(frame)
            if not should_continue:
                break

            time.sleep(args.interval)
    finally:
        source.close()
        collector.print_stats()


if __name__ == "__main__":
    main()
