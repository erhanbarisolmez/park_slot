"""Kuşbakışı (bird's-eye) görünümde yüksek çözünürlüklü slot işaretleme.

Ham karedeki perspektif açısı yüzünden ince/dar slot çizme problemini
çözer: perspective_transform'dan geri çıkarılan 4 zemin noktasıyla ham
kareyi yüksek çözünürlüklü bir kuşbakışı tuvale warp eder, mevcut slotları
bu tuvalde önceden yüklü gösterir (SlotAnnotator ile düzenleme/ekleme
yapılabilir), sonra sonucu ham piksel uzayına geri çevirip kaydeder.

Kuşbakışında araçlar/sınırlar düz ve orantılı göründüğü için, kamerada
birbirine çok yakın/dar açılı slotlar arasına (örn. iki araç arasındaki
üçüncü bir boşluk) hassas sınır çizmek ham karedekinden çok daha kolaydır.

Önkoşul: `python tools/mark_perspective.py --camera <id>` ile
perspective_transform zaten ayarlanmış olmalı (bu araç onu değiştirmez,
sadece okur - canlı minimap özelliği bozulmaz).

Kullanım:
    python tools/annotate_birdseye.py --camera cam2
    python tools/annotate_birdseye.py --camera cam2 --width 1600 --height 1000
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

from park_slot.annotation.gui import SlotAnnotator
from park_slot.annotation.schema import CameraSlotConfig, SlotDefinition
from park_slot.annotation.transform import compute_perspective_matrix, warp_image, warp_points
from park_slot.capture.file import FileSource
from park_slot.capture.youtube import YouTubeSource
from park_slot.inference.overlay import MINIMAP_HEIGHT, MINIMAP_WIDTH

DEFAULT_WIDTH = 1400
DEFAULT_HEIGHT = 900
# Ham kare sınırlarının bu kadar (piksel) dışına taşan noktalar için uyar -
# tuval kenarına çok yakın çizilmiş olabilir (ekstrapolasyon dengesiz hale gelir).
OUT_OF_BOUNDS_MARGIN = 20


def get_frame(source_path: str):
    """Kaynaktan ilk kareyi al (tools/annotate.py ve tools/mark_perspective.py
    ile aynı kalıp - tools/ bir paket olmadığı için küçük yardımcı tekrarlanır)."""
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


def recover_ground_points(perspective_transform: list[list[float]]) -> list[list[float]]:
    """Kayıtlı perspective_transform'dan orijinal 4 zemin noktasını geri çıkar.

    mark_perspective.py'nin compute_homography'si bu matrisi TAM OLARAK
    [[0,0],[MINIMAP_WIDTH-1,0],[MINIMAP_WIDTH-1,MINIMAP_HEIGHT-1],[0,MINIMAP_HEIGHT-1]]
    köşelerini dst olarak kullanarak kurduğu için, tersini alırken de aynı
    köşeleri kullanmak gerekir (MINIMAP_WIDTH/HEIGHT değil - -1 farkı önemli).

    Returns:
        [sol-uzak, sag-uzak, sag-yakin, sol-yakin] = [TL, TR, BR, BL] sırasında.
    """
    matrix = np.array(perspective_transform, dtype=np.float64)
    inv = np.linalg.inv(matrix)
    minimap_corners = [
        [0, 0],
        [MINIMAP_WIDTH - 1, 0],
        [MINIMAP_WIDTH - 1, MINIMAP_HEIGHT - 1],
        [0, MINIMAP_HEIGHT - 1],
    ]
    return warp_points(minimap_corners, inv)


def main():
    parser = argparse.ArgumentParser(description="Kuşbakışı görünümde slot işaretleme")
    parser.add_argument("--camera", default="cam1", help="Kamera ID (varsayılan: cam1)")
    parser.add_argument("--source", default=None, help="Video/YouTube URL (verilmezse kayıtlı source_url kullanılır)")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help=f"Kuşbakışı tuval genişliği (varsayılan: {DEFAULT_WIDTH})")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help=f"Kuşbakışı tuval yüksekliği (varsayılan: {DEFAULT_HEIGHT})")
    parser.add_argument("--output", default=None, help="Çıktı dosyası (varsayılan: config/slots/<camera>.json)")
    args = parser.parse_args()

    save_path = Path(args.output or f"config/slots/{args.camera}.json")
    if not save_path.exists():
        parser.error(
            f"{save_path} bulunamadı. Önce 'python tools/annotate.py --camera {args.camera}' "
            "ile slotları işaretlemelisin."
        )

    with open(save_path, "r", encoding="utf-8") as f:
        existing = json.load(f)

    perspective_transform = existing.get("perspective_transform")
    if not perspective_transform:
        parser.error(
            f"{save_path} içinde perspective_transform yok. Önce "
            f"'python tools/mark_perspective.py --camera {args.camera}' çalıştırılmalı."
        )

    source = args.source or existing.get("source_url", "")
    if not source:
        parser.error("--source verilmedi ve kayıtlı bir source_url bulunamadı.")

    print(f"Kaynak: {source}")
    print("İlk kare alınıyor...")
    frame = get_frame(source)
    raw_h, raw_w = frame.shape[:2]

    stored_w, stored_h = existing.get("image_width"), existing.get("image_height")
    if (stored_w, stored_h) != (raw_w, raw_h):
        print(
            f"UYARI: kayıtlı çözünürlük {stored_w}x{stored_h}, şu anki kare {raw_w}x{raw_h} "
            "- perspective_transform ve mevcut slot koordinatları uyumsuz olabilir."
        )

    ground_points = recover_ground_points(perspective_transform)
    hr_matrix = compute_perspective_matrix(ground_points, args.width, args.height)
    canvas = warp_image(frame, hr_matrix, args.width, args.height)

    existing_slots = [SlotDefinition(**s) for s in existing.get("slots", [])]
    initial_slots = []
    for slot in existing_slots:
        bev_poly = warp_points(slot.polygon, hr_matrix)
        cx = sum(p[0] for p in bev_poly) / len(bev_poly)
        cy = sum(p[1] for p in bev_poly) / len(bev_poly)
        if not (0 <= cx <= args.width and 0 <= cy <= args.height):
            print(
                f"UYARI: slot {slot.label} (id={slot.id}) kuşbakışı tuvalin dışına düşüyor "
                "- mark_perspective.py'deki zemin dörtgeni bu slotu kapsamıyor olabilir."
            )
        initial_slots.append(SlotDefinition(id=slot.id, label=slot.label, polygon=bev_poly))

    print(f"{len(initial_slots)} mevcut slot kuşbakışı tuvalde önceden yüklendi.")
    if initial_slots:
        ids = ", ".join(f"{s.label}(id={s.id})" for s in initial_slots)
        print(f"Mevcut slotlar: {ids}")
        print(
            "Not: sadece son slottan geriye doğru 'u' ile geri alabilirsin - "
            "ortadaki bir slotu düzeltmek için ondan sonraki tüm slotları da "
            "geri alıp sırayla yeniden çizmen gerekir."
        )

    source_url_to_save = source if source.startswith("http") else existing.get("source_url", "")

    with tempfile.TemporaryDirectory() as tmp:
        scratch_path = Path(tmp) / "birdseye_scratch.json"
        annotator = SlotAnnotator(
            canvas, camera_id=args.camera, source_url=source_url_to_save, initial_slots=initial_slots,
        )
        # save_path'i gerçek config dosyasına DEĞİL, geçici bir dosyaya veriyoruz -
        # aksi halde SlotAnnotator.run()'da 's' tuşu, kuşbakışı tuval boyutlarıyla
        # ve kuşbakışı-uzayı koordinatlarıyla doğrudan gerçek dosyayı bozardı.
        bev_slots = annotator.run(save_path=scratch_path)

    hr_inv = np.linalg.inv(hr_matrix)
    final_slots = []
    for slot in bev_slots:
        raw_poly = warp_points(slot.polygon, hr_inv)
        out_of_bounds = any(
            not (-OUT_OF_BOUNDS_MARGIN <= x <= raw_w + OUT_OF_BOUNDS_MARGIN
                 and -OUT_OF_BOUNDS_MARGIN <= y <= raw_h + OUT_OF_BOUNDS_MARGIN)
            for x, y in raw_poly
        )
        if out_of_bounds:
            print(
                f"UYARI: slot {slot.label} (id={slot.id}) ham kare sınırlarının dışına "
                f"düşüyor ({raw_poly}) - tuval kenarına çok yakın çizilmiş olabilir."
            )
        final_slots.append(SlotDefinition(id=slot.id, label=slot.label, polygon=raw_poly))

    config = CameraSlotConfig(
        camera_id=args.camera,
        source_url=source_url_to_save,
        image_width=raw_w,
        image_height=raw_h,
        slots=final_slots,
        perspective_transform=perspective_transform,  # değişmeden korunur
    )

    with open(save_path, "w", encoding="utf-8") as f:
        f.write(config.model_dump_json(indent=2))

    old_ids = {s.id for s in existing_slots}
    new_ids = {s.id for s in final_slots} - old_ids
    print(f"\nKaydedildi: {save_path}")
    print(f"Slot sayısı: {len(existing_slots)} -> {len(final_slots)}")
    if new_ids:
        print(f"Yeni slot id'leri: {sorted(new_ids)}")


if __name__ == "__main__":
    main()
