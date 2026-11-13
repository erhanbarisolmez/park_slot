"""Operatör override'larından gelen onay bekleyen eğitim verisini gözden geçir.

/live sayfasında bir operatör bir slotu elle "dolu"/"boş" diye işaretlediğinde
(bkz. api/routes.py set_slot_override, inference/pipeline.py
Pipeline.save_override_label), o anki kare otomatik olarak kırpılıp
data/labeled_cam{N}/pending/{empty,occupied}/ altına kaydedilir - ama DOĞRUDAN
asıl eğitim setine (data/labeled_cam{N}/{empty,occupied}/) girmez. Operatör
yanlış tıklayabilir; bu araç her pending görüntüyü insana gösterip onay/red
kararını bekler, sadece onaylananlar eğitim setine taşınır.

Kullanım:
    python tools/review_overrides.py --camera cam1
    python tools/review_overrides.py --camera cam2 --pending-dir /tmp/cam2_pending

Tuşlar:
    a = onayla (eğitim setine taşı)
    r = reddet (sil - operatör hatası, saklamaya değmez)
    s = atla (pending'de kalır, sonra tekrar bakılabilir)
    q / ESC = çık
"""

import argparse
from pathlib import Path

import cv2

from park_slot.services.override_review import approve, list_pending, pending_dir_for, reject

PREVIEW_HEIGHT = 480


def _status_text(idx: int, total: int, path: Path) -> str:
    label = path.parent.name
    return f"[{idx}/{total}] {label} | {path.name} | a=onayla r=reddet s=atla q=cik"


def main() -> None:
    parser = argparse.ArgumentParser(description="Onay bekleyen override verisini gözden geçir")
    parser.add_argument("--camera", default=None, help="Kamera id (örn. cam1) - data/labeled_cam{N}/pending kullanılır")
    parser.add_argument("--pending-dir", default=None, help="Pending klasörünü doğrudan belirt (--camera yerine)")
    args = parser.parse_args()

    if not args.camera and not args.pending_dir:
        parser.error("--camera veya --pending-dir verilmeli")

    pending_dir = Path(args.pending_dir) if args.pending_dir else pending_dir_for(args.camera)
    if not pending_dir.exists():
        print(f"Pending klasörü yok: {pending_dir} (henüz hiç override yapılmamış olabilir)")
        return

    images = list_pending(pending_dir)
    if not images:
        print("Onay bekleyen görüntü yok.")
        return

    print(f"{len(images)} görüntü gözden geçirilecek: {pending_dir}\n")

    counts = {"approved": 0, "rejected": 0, "skipped": 0}
    total = len(images)

    for idx, path in enumerate(images, start=1):
        image = cv2.imread(str(path))
        if image is None:
            print(f"Okunamadı, atlandı: {path}")
            continue

        h, w = image.shape[:2]
        scale = PREVIEW_HEIGHT / h
        preview = cv2.resize(image, (int(w * scale), PREVIEW_HEIGHT), interpolation=cv2.INTER_NEAREST)
        cv2.putText(
            preview, _status_text(idx, total, path), (5, 20),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1,
        )

        cv2.imshow("Override Gozden Gecir", preview)
        key = cv2.waitKey(0) & 0xFF

        if key == ord("a"):
            approve(path)
            counts["approved"] += 1
        elif key == ord("r"):
            reject(path)
            counts["rejected"] += 1
        elif key == ord("s"):
            counts["skipped"] += 1
        elif key == ord("q") or key == 27:
            break

    cv2.destroyAllWindows()

    print("\nGözden geçirme tamamlandı:")
    print(f"  Onaylanan: {counts['approved']}")
    print(f"  Reddedilen: {counts['rejected']}")
    print(f"  Atlanan:   {counts['skipped']}")


if __name__ == "__main__":
    main()
