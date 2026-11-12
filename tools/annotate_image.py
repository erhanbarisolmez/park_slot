"""Tek bir görüntü üzerinde slot işaretleme.

Kullanım:
    python tools/annotate_image.py --image data/sample_frame.jpg --camera cam1

    # Mevcut config/slots/<camera>.json varsa otomatik yüklenir (düzenleme modu):
    # eski slotlar yeşil çizilir, silmek için üzerine sağ tık, yeni slot eklemek
    # için her zamanki gibi sol tık + Enter.
    python tools/annotate_image.py --image data/sample_frame.jpg --camera cam2 --edit

Kontroller:
    Sol tık: Nokta ekle
    Sağ tık (çizim sırasında): Son noktayı geri al
    Sağ tık (çizim yokken, --edit): Üzerine tıklanan mevcut slotu sil
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
from park_slot.annotation.schema import SlotDefinition


def main():
    parser = argparse.ArgumentParser(description="Görüntü üzerinde slot işaretleme")
    parser.add_argument("--image", required=True, help="Görüntü dosyası yolu")
    parser.add_argument("--camera", default="cam1", help="Kamera ID")
    parser.add_argument(
        "--edit", action="store_true",
        help="config/slots/<camera>.json'daki mevcut slotları yükle (silinmedikçe korunur)",
    )
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
    initial_slots: list[SlotDefinition] = []
    if Path(save_path).exists():
        with open(save_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing_source_url = existing.get("source_url", "")
        if args.edit:
            initial_slots = [SlotDefinition(**s) for s in existing.get("slots", [])]
            print(f"Düzenleme modu: {len(initial_slots)} mevcut slot yüklendi (silmek için üzerine sağ tık).")

    annotator = SlotAnnotator(
        image, camera_id=args.camera, source_url=existing_source_url, initial_slots=initial_slots,
    )
    slots = annotator.run(save_path=save_path)

    print(f"\nToplam {len(slots)} slot işaretlendi.")
    if slots:
        print(f"Kaydedildi: {save_path}")


if __name__ == "__main__":
    main()
