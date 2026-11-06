"""Slot işaretleme aracı.

Kullanım:
    python tools/annotate.py --source video.mp4 --camera cam1
    python tools/annotate.py --source https://youtube.com/watch?v=... --camera cam1

    # URL bir kere verilip kaydedildikten sonra (cam1.json'a yazılır),
    # sonraki çalıştırmalarda --source tekrar yazmaya gerek yok:
    python tools/annotate.py --camera cam1
"""

import argparse
import json
import sys
from pathlib import Path

import cv2

from park_slot.capture.file import FileSource
from park_slot.capture.youtube import YouTubeSource
from park_slot.annotation.gui import SlotAnnotator


def get_frame(source_path: str) -> any:
    """Kaynaktan ilk kareyi al."""
    if source_path.startswith("http"):
        source = YouTubeSource(source_path)
    else:
        source = FileSource(source_path)

    source.open()
    frame = source.read()
    source.close()

    if frame is None:
        print("Hata: Kare okunamadı!")
        sys.exit(1)

    return frame


def main():
    parser = argparse.ArgumentParser(description="Park slotu işaretleme aracı")
    parser.add_argument(
        "--source", default=None,
        help="Video dosyası veya YouTube URL (verilmezse config/slots/<camera>.json içindeki kayıtlı source_url kullanılır)",
    )
    parser.add_argument("--camera", default="cam1", help="Kamera ID (varsayılan: cam1)")
    parser.add_argument("--output", default=None, help="Çıktı dosyası (varsayılan: config/slots/<camera>.json)")
    args = parser.parse_args()

    save_path = args.output or f"config/slots/{args.camera}.json"

    existing_source_url = ""
    if Path(save_path).exists():
        with open(save_path, "r", encoding="utf-8") as f:
            existing_source_url = json.load(f).get("source_url", "")

    source = args.source or existing_source_url
    if not source:
        parser.error(
            "--source verilmedi ve kayıtlı bir source_url bulunamadı. "
            "İlk çalıştırmada --source ile URL/dosya yolu belirtmelisiniz."
        )

    print(f"Kaynak: {source}")
    print("İlk kare alınıyor...")

    frame = get_frame(source)

    print(f"Kare boyutu: {frame.shape[1]}x{frame.shape[0]}")
    print("Annotator başlatılıyor...")

    source_url_to_save = source if source.startswith("http") else existing_source_url
    annotator = SlotAnnotator(frame, camera_id=args.camera, source_url=source_url_to_save)
    slots = annotator.run(save_path=save_path)

    print(f"Toplam {len(slots)} slot işaretlendi.")


if __name__ == "__main__":
    main()
