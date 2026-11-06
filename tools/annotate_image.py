"""Tek bir görüntü üzerinde slot işaretleme.

Kullanım:
    python tools/annotate_image.py --image data/sample_frame.jpg --camera cam1

Kontroller:
    Sol tık: Nokta ekle
    Sağ tık: Son noktayı geri al
    Enter: Slot'u kaydet, sonrakine geç
    U: Son slot'u geri al
    S: JSON olarak kaydet ve kapat
    Q / ESC: Kaydetmeden çık
"""

import argparse
import json
from pathlib import Path

import cv2
from park_slot.annotation.gui import SlotAnnotator


def main():
    parser = argparse.ArgumentParser(description="Görüntü üzerinde slot işaretleme")
    parser.add_argument("--image", required=True, help="Görüntü dosyası yolu")
    parser.add_argument("--camera", default="cam1", help="Kamera ID")
    args = parser.parse_args()

    image = cv2.imread(args.image)
    if image is None:
        print(f"Hata: Görüntü okunamadı: {args.image}")
        return

    print(f"Görüntü: {args.image} ({image.shape[1]}x{image.shape[0]})")
    print("Slotları fare ile işaretleyin.")
    print("Sol tık=nokta ekle | Enter=kaydet | S=dosyala ve kapat | Q=kaydetmeden çık")

    save_path = f"config/slots/{args.camera}.json"
    existing_source_url = ""
    if Path(save_path).exists():
        with open(save_path, "r", encoding="utf-8") as f:
            existing_source_url = json.load(f).get("source_url", "")

    annotator = SlotAnnotator(image, camera_id=args.camera, source_url=existing_source_url)
    slots = annotator.run(save_path=save_path)

    print(f"\nToplam {len(slots)} slot işaretlendi.")
    if slots:
        print(f"Kaydedildi: {save_path}")


if __name__ == "__main__":
    main()
