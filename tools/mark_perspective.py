"""Kuşbakışı mini-harita için zemin düzlemi referans noktalarını işaretleme.

Kamera açısı/çarpıklığından bağımsız bir kuşbakışı (top-down) mini-harita
çizebilmek için, park alanının zemin düzleminde 4 referans köşe noktası
(bir dörtgen) işaretlenir. Bu 4 nokta ile kanonik bir dikdörtgene homografi
hesaplanır ve ilgili kameranın slot config dosyasındaki `perspective_transform`
alanına kaydedilir (slotlara dokunulmaz).

Noktaları TAM OLARAK şu sırayla tıkla (park alanının zemin sınırları):
  1. Sol-uzak köşe
  2. Sağ-uzak köşe
  3. Sağ-yakın köşe
  4. Sol-yakın köşe

Kullanım:
    python tools/mark_perspective.py --camera cam1
    python tools/mark_perspective.py --camera cam1 --source https://youtube.com/watch?v=...
"""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from park_slot.capture.file import FileSource
from park_slot.capture.youtube import YouTubeSource
from park_slot.inference.overlay import MINIMAP_WIDTH, MINIMAP_HEIGHT

ORDER_LABELS = ["Sol-uzak", "Sag-uzak", "Sag-yakin", "Sol-yakin"]


def get_frame(source_path: str):
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


class GroundPointPicker:
    """4 zemin referans noktasını fare ile işaretleme aracı."""

    def __init__(self, image: np.ndarray):
        self._image = image.copy()
        self._points: list[list[int]] = []
        self._window_name = "Zemin Referans Noktalari"

    def _mouse_callback(self, event, x, y, flags, param) -> None:
        if event == cv2.EVENT_LBUTTONDOWN and len(self._points) < 4:
            self._points.append([x, y])
        elif event == cv2.EVENT_RBUTTONDOWN and self._points:
            self._points.pop()

    def _draw(self) -> np.ndarray:
        canvas = self._image.copy()

        for i, pt in enumerate(self._points):
            cv2.circle(canvas, tuple(pt), 6, (0, 0, 255), -1)
            cv2.putText(canvas, f"{i + 1}", (pt[0] + 8, pt[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        if len(self._points) > 1:
            pts = np.array(self._points, dtype=np.int32)
            closed = len(self._points) == 4
            cv2.polylines(canvas, [pts], closed, (0, 255, 0), 2)

        next_label = ORDER_LABELS[len(self._points)] if len(self._points) < 4 else "Tamam"
        info = f"Nokta {len(self._points)}/4 | Sirada: {next_label}"
        cv2.putText(canvas, info, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(canvas, "Sol:Ekle | Sag:Geri | S:Kaydet (4 nokta gerekli) | Q:Cik",
                    (10, canvas.shape[0] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        return canvas

    def run(self) -> list[list[int]] | None:
        cv2.namedWindow(self._window_name)
        cv2.setMouseCallback(self._window_name, self._mouse_callback)

        result = None
        while True:
            cv2.imshow(self._window_name, self._draw())
            key = cv2.waitKey(30) & 0xFF

            if key == ord("s"):
                if len(self._points) == 4:
                    result = list(self._points)
                    break
                print(f"4 nokta gerekli, şu an {len(self._points)} var.")
            elif key == ord("q") or key == 27:
                break

        cv2.destroyWindow(self._window_name)
        return result


def compute_homography(src_points: list[list[int]]) -> list[list[float]]:
    """4 zemin noktasından kanonik kuşbakışı dikdörtgene homografi hesapla."""
    src = np.array(src_points, dtype=np.float32)
    dst = np.array(
        [[0, 0], [MINIMAP_WIDTH - 1, 0], [MINIMAP_WIDTH - 1, MINIMAP_HEIGHT - 1], [0, MINIMAP_HEIGHT - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    return matrix.tolist()


def main():
    parser = argparse.ArgumentParser(description="Kuşbakışı mini-harita için zemin referans noktalarını işaretle")
    parser.add_argument("--camera", default="cam1", help="Kamera ID (varsayılan: cam1)")
    parser.add_argument("--source", default=None, help="Video/YouTube URL (verilmezse kayıtlı source_url kullanılır)")
    args = parser.parse_args()

    slots_path = Path(f"config/slots/{args.camera}.json")
    if not slots_path.exists():
        parser.error(f"{slots_path} bulunamadı. Önce 'python tools/annotate.py --camera {args.camera}' ile slotları işaretlemelisin.")

    with open(slots_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)

    source = args.source or config_data.get("source_url", "")
    if not source:
        parser.error("--source verilmedi ve kayıtlı bir source_url bulunamadı.")

    print(f"Kaynak: {source}")
    print("İlk kare alınıyor...")
    frame = get_frame(source)

    print("Zemin düzleminde 4 referans köşe noktasını işaretle (sırayla):")
    for i, label in enumerate(ORDER_LABELS, 1):
        print(f"  {i}. {label}")

    picker = GroundPointPicker(frame)
    points = picker.run()

    if points is None:
        print("İptal edildi, kaydedilmedi.")
        return

    config_data["perspective_transform"] = compute_homography(points)

    with open(slots_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)

    print(f"Kaydedildi: {slots_path} (perspective_transform güncellendi, {len(config_data.get('slots', []))} slot korundu)")


if __name__ == "__main__":
    main()
