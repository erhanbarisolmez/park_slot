"""Rastgele (veya belirli) bir geçmiş zamandan kare çekip modelin
tahminlerini görsel olarak doğrulama aracı.

Canlı akışı dakikalarca izlemek yerine, YouTube'un DVR arşivinden rastgele
bir anı çekip modelin o anki ham tahminlerini (StateStore/hysteresis
uygulanmadan - doğrudan model çıktısı) overlay olarak çizer ve bir PNG
olarak kaydeder. Böylece gerçek sahneye bakıp model tahminini gözle
karşılaştırarak doğruluğu hızlıca denetleyebilirsin.

Kullanım:
    python tools/verify_random_frame.py --camera cam2
    python tools/verify_random_frame.py --camera cam2 --minutes-ago 180
    python tools/verify_random_frame.py --camera cam2 --max-minutes-ago 4320
"""

import argparse
import random
from datetime import datetime
from pathlib import Path

import cv2

from park_slot.annotation.schema import ParkingStatus, SlotState, SlotStatus
from park_slot.capture.youtube_history import fetch_historical_frame
from park_slot.inference.engine import InferenceEngine
from park_slot.inference.overlay import draw_overlay
from park_slot.services.slot_manager import SlotManager


def main():
    parser = argparse.ArgumentParser(description="Geçmiş bir kareden model tahminini görsel doğrula")
    parser.add_argument("--camera", default="cam1", help="Kamera ID (varsayılan: cam1)")
    parser.add_argument("--slots", default=None, help="Slot config yolu (varsayılan: config/slots/<camera>.json)")
    parser.add_argument("--checkpoint", default=None, help="Model yolu (varsayılan: data/models_<camera>/best.pth)")
    parser.add_argument(
        "--minutes-ago", type=float, default=None,
        help="Belirli bir an (dakika önce) - verilmezse 0 ile --max-minutes-ago arasından rastgele seçilir",
    )
    parser.add_argument(
        "--max-minutes-ago", type=float, default=4320,
        help="Rastgele seçimin üst sınırı (dakika, varsayılan 4320 = 3 gün)",
    )
    parser.add_argument("--output", default=None, help="Çıktı PNG yolu (varsayılan: data/verify_snapshots/ altına otomatik isim)")
    args = parser.parse_args()

    slots_path = args.slots or f"config/slots/{args.camera}.json"
    checkpoint = args.checkpoint or f"data/models_{args.camera}/best.pth"

    print(f"Slotlar: {slots_path}")
    print(f"Model: {checkpoint}")
    slot_manager = SlotManager(slots_path, input_size=64)
    engine = InferenceEngine(checkpoint_path=checkpoint, device="cpu")

    minutes_ago = args.minutes_ago if args.minutes_ago is not None else random.uniform(0, args.max_minutes_ago)
    print(f"Seçilen zaman: {minutes_ago:.1f} dakika önce (~{minutes_ago / 60:.1f} saat)")

    frame, captured_at = fetch_historical_frame(slot_manager.config.source_url, minutes_ago, height=720)
    print(f"Kare zamanı (tahmini): {datetime.fromtimestamp(captured_at):%Y-%m-%d %H:%M:%S}")

    slots, tensors = slot_manager.get_tensors(frame)
    results = engine.predict_batch(tensors)

    slot_states = []
    occupied = empty = unknown = 0
    for slot_def, (status, confidence) in zip(slots, results):
        slot_states.append(SlotState(
            id=slot_def.id, label=slot_def.label, status=status,
            confidence=confidence, raw_status=status,
        ))
        if status == SlotStatus.OCCUPIED:
            occupied += 1
        elif status == SlotStatus.EMPTY:
            empty += 1
        else:
            unknown += 1

    parking_status = ParkingStatus(
        camera_id=args.camera, total_slots=len(slot_states),
        occupied=occupied, empty=empty, unknown=unknown, slots=slot_states,
    )

    polygons = {s.id: s.polygon for s in slot_manager.slots}
    overlay_img = draw_overlay(
        frame, parking_status, polygons,
        perspective_transform=slot_manager.config.perspective_transform,
    )

    if args.output:
        out_path = Path(args.output)
    else:
        out_dir = Path("data/verify_snapshots")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{args.camera}_{int(minutes_ago)}dk_once.png"

    cv2.imwrite(str(out_path), overlay_img)
    print(f"\nKaydedildi: {out_path}")
    print(f"Sonuç: toplam={len(slot_states)} boş={empty} dolu={occupied} bilinmiyor={unknown}")


if __name__ == "__main__":
    main()
