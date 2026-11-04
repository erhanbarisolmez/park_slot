"""Slot işaretleme aracı.

Kullanım:
    python tools/annotate.py --source video.mp4 --camera cam1
    python tools/annotate.py --source https://youtube.com/watch?v=... --camera cam1
"""

import argparse
import sys

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
    parser.add_argument("--source", required=True, help="Video dosyası veya YouTube URL")
    parser.add_argument("--camera", default="cam1", help="Kamera ID (varsayılan: cam1)")
    parser.add_argument("--output", default=None, help="Çıktı dosyası (varsayılan: config/slots/<camera>.json)")
    args = parser.parse_args()

    print(f"Kaynak: {args.source}")
    print("İlk kare alınıyor...")

    frame = get_frame(args.source)

    print(f"Kare boyutu: {frame.shape[1]}x{frame.shape[0]}")
    print("Annotator başlatılıyor...")

    save_path = args.output or f"config/slots/{args.camera}.json"
    annotator = SlotAnnotator(frame, camera_id=args.camera)
    slots = annotator.run(save_path=save_path)

    print(f"Toplam {len(slots)} slot işaretlendi.")


if __name__ == "__main__":
    main()
